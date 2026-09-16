"""
app.py
Flask backend for the facial-recognition attendance system.
Adds: GPS geofencing (reject check-in/out outside the office radius) and
cloud-DB support (via database.py / DATABASE_URL).

Routes:
  GET  /                    -> main page (scan / register / reports tabs)
  GET  /api/config          -> geofence settings for the frontend (office coords, radius)
  GET  /api/employees       -> list registered employees
  POST /api/register        -> register a new employee (name, employee_code, image)
  DELETE /api/employees/<id>-> remove an employee
  POST /api/scan            -> recognize a face + check geofence, mark check-in/out
  GET  /api/attendance      -> attendance records (optional ?date=YYYY-MM-DD)
  GET  /api/export          -> download attendance as CSV
"""

import os
import base64
import io
import csv
import math
from datetime import date

import numpy as np
import face_recognition
from flask import Flask, request, jsonify, render_template, Response

import database as db

app = Flask(__name__)

MATCH_TOLERANCE = 0.5

# ---- Geofence configuration (env vars, editable without a code change) ----
OFFICE_LAT = os.environ.get("OFFICE_LAT")
OFFICE_LNG = os.environ.get("OFFICE_LNG")
GEOFENCE_RADIUS_METERS = float(os.environ.get("GEOFENCE_RADIUS_METERS", "150"))
GEOFENCE_ENABLED = OFFICE_LAT is not None and OFFICE_LNG is not None

if GEOFENCE_ENABLED:
    OFFICE_LAT = float(OFFICE_LAT)
    OFFICE_LNG = float(OFFICE_LNG)

db.init_db()

if not os.environ.get("DATABASE_URL"):
    print(
        "WARNING: DATABASE_URL is not set — using local SQLite. "
        "On most cloud hosts this file is wiped on every restart/redeploy. "
        "Set DATABASE_URL to a permanent Postgres database (e.g. from neon.tech) "
        "for data that survives restarts."
    )


def haversine_meters(lat1, lng1, lat2, lng2):
    R = 6371000  # Earth radius, meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def decode_image(data_url: str) -> np.ndarray:
    header, encoded = data_url.split(",", 1)
    img_bytes = base64.b64decode(encoded)
    return face_recognition.load_image_file(io.BytesIO(img_bytes))


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/config", methods=["GET"])
def config():
    return jsonify({
        "geofence_enabled": GEOFENCE_ENABLED,
        "office_lat": OFFICE_LAT if GEOFENCE_ENABLED else None,
        "office_lng": OFFICE_LNG if GEOFENCE_ENABLED else None,
        "radius_meters": GEOFENCE_RADIUS_METERS,
    })


@app.route("/api/employees", methods=["GET"])
def list_employees():
    return jsonify(db.get_all_employees())


@app.route("/api/register", methods=["POST"])
def register_employee():
    payload = request.get_json(force=True)
    name = (payload.get("name") or "").strip()
    employee_code = (payload.get("employee_code") or "").strip()
    image_data = payload.get("image")

    if not name or not employee_code or not image_data:
        return jsonify({"error": "name, employee_code and image are required"}), 400

    if db.employee_code_exists(employee_code):
        return jsonify({"error": f"Employee code '{employee_code}' is already registered"}), 409

    try:
        image = decode_image(image_data)
    except Exception:
        return jsonify({"error": "Could not decode image"}), 400

    face_locations = face_recognition.face_locations(image)
    if len(face_locations) == 0:
        return jsonify({"error": "No face detected. Make sure your face is clearly visible and try again."}), 422
    if len(face_locations) > 1:
        return jsonify({"error": "Multiple faces detected. Only one person should be in frame."}), 422

    encodings = face_recognition.face_encodings(image, known_face_locations=face_locations)
    encoding = encodings[0]

    existing = db.get_all_encodings()
    if existing:
        known_encodings = [e[3] for e in existing]
        matches = face_recognition.compare_faces(known_encodings, encoding, tolerance=MATCH_TOLERANCE)
        if any(matches):
            matched = existing[matches.index(True)]
            return jsonify({"error": f"This face is already registered as '{matched[1]}' ({matched[2]})"}), 409

    db.add_employee(name, employee_code, encoding)
    return jsonify({"status": "registered", "name": name, "employee_code": employee_code})


@app.route("/api/employees/<int:employee_id>", methods=["DELETE"])
def remove_employee(employee_id):
    db.delete_employee(employee_id)
    return jsonify({"status": "deleted"})


@app.route("/api/scan", methods=["POST"])
def scan_face():
    payload = request.get_json(force=True)
    image_data = payload.get("image")
    action = payload.get("action", "check_in")
    lat = payload.get("lat")
    lng = payload.get("lng")

    if not image_data:
        return jsonify({"error": "image is required"}), 400

    # ---- Geofence check first: no point running face recognition if they're not even nearby ----
    if GEOFENCE_ENABLED:
        if lat is None or lng is None:
            return jsonify({"matched": False, "message": "Location permission is required to check in/out."}), 200
        distance = haversine_meters(OFFICE_LAT, OFFICE_LNG, float(lat), float(lng))
        if distance > GEOFENCE_RADIUS_METERS:
            return jsonify({
                "matched": False,
                "message": f"You're {int(distance)}m from the office — must be within {int(GEOFENCE_RADIUS_METERS)}m to check in/out.",
            }), 200

    known = db.get_all_encodings()
    if not known:
        return jsonify({"error": "No employees registered yet"}), 404

    try:
        image = decode_image(image_data)
    except Exception:
        return jsonify({"error": "Could not decode image"}), 400

    face_locations = face_recognition.face_locations(image)
    if len(face_locations) == 0:
        return jsonify({"matched": False, "message": "No face detected"}), 200

    encodings = face_recognition.face_encodings(image, known_face_locations=face_locations)
    probe = encodings[0]

    known_encodings = [e[3] for e in known]
    distances = face_recognition.face_distance(known_encodings, probe)
    best_idx = int(np.argmin(distances))
    best_distance = float(distances[best_idx])

    if best_distance > MATCH_TOLERANCE:
        return jsonify({"matched": False, "message": "Face not recognized"}), 200

    employee_id, name, employee_code, _ = known[best_idx]

    lat_f = float(lat) if lat is not None else None
    lng_f = float(lng) if lng is not None else None

    if action == "check_out":
        result = db.mark_check_out(employee_id, lat_f, lng_f)
    else:
        result = db.mark_check_in(employee_id, lat_f, lng_f)

    return jsonify({
        "matched": True,
        "name": name,
        "employee_code": employee_code,
        "result": result,
        "confidence": round((1 - best_distance) * 100, 1),
    })


@app.route("/api/attendance", methods=["GET"])
def attendance():
    date_filter = request.args.get("date")
    return jsonify(db.get_attendance(date_filter))


@app.route("/api/export", methods=["GET"])
def export_csv():
    date_filter = request.args.get("date")
    records = db.get_attendance(date_filter)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Name", "Employee Code", "Date", "Check-in", "Check-out"])
    for r in records:
        writer.writerow([r["name"], r["employee_code"], r["date"], r["check_in_time"] or "", r["check_out_time"] or ""])

    filename = f"attendance_{date_filter or date.today().isoformat()}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename={filename}"},
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
