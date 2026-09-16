# Cosmos Groups Attendance

A self-hosted attendance system for office employees, accessible from any
phone browser. Employees check in / check out by looking at the camera —
and it only works when they're physically near the office (GPS geofence).

## How it works
- **Scan** — employee opens the site on their phone, camera + GPS activate,
  they tap Check In / Check Out. The backend verifies (1) they're within the
  configured radius of the office and (2) their face matches a registered
  employee, then logs the time.
- **Register** — admin captures an employee's face once + name + employee
  code. Only the face's numeric "encoding" is stored, not the photo.
- **Reports** — view/export attendance by date.

Face matching runs server-side (`face_recognition` / dlib) — this can't be
done as a pure static webpage, which is why it's a real backend you deploy,
not just a link you open.

## Permanent data storage — read this first

Data only survives restarts if `DATABASE_URL` points to a **real,
permanent** Postgres database. Two things to know:

- Without `DATABASE_URL` set, the app falls back to a local SQLite file.
  On almost every cloud host (Render, Railway, Heroku-style platforms),
  local files are wiped every time the app restarts or redeploys. Fine for
  testing on your own laptop, **not** fine for production.
- Render's own free Postgres database is **not** actually permanent — it
  expires and is auto-deleted 30 days after creation. So this project does
  **not** use it.

Instead, use a provider with a genuinely permanent free tier:

**[Neon](https://neon.tech)** (recommended) or **[Supabase](https://supabase.com)**
1. Sign up, create a project/database (free tier, no card required).
2. Copy the connection string it gives you (looks like
   `postgresql://user:password@host/dbname`).
3. Set that as `DATABASE_URL` wherever you deploy the app. That's it —
   this database is not tied to your app host, so even deleting and
   redeploying the web service never touches the data.

## Deploying online (Render — free tier, for the app itself)

1. Push this folder to a GitHub repo.
2. Create your permanent database first (Neon or Supabase, above) and copy
   its connection string.
3. Go to [render.com](https://render.com) → **New → Blueprint** → connect
   your repo. Render reads `render.yaml` and creates a web service running
   `gunicorn app:app`.
4. In the service's **Environment** tab, set:
   - `DATABASE_URL` — the Neon/Supabase connection string from step 2.
   - `OFFICE_LAT` / `OFFICE_LNG` — your office's coordinates. Easiest way:
     open Google Maps, right-click your office location, click the
     coordinates to copy them (e.g. `9.9252, 78.1198`).
   - `GEOFENCE_RADIUS_METERS` — how far (in meters) from that point
     check-in is allowed. 100–200m is reasonable for one building.
5. Deploy. Render gives you a URL like
   `https://cosmos-groups-attendance.onrender.com` — that's it, it's live,
   with HTTPS (required for camera/GPS access on phones).

Render's free *web service* is fine to use here even though its free
*database* isn't — the app itself is stateless; all your data lives in
Neon/Supabase regardless of how often the web service restarts, sleeps
(free tier spins down after ~15 min idle, wakes on the next request), or
gets redeployed.

Railway, Fly.io, or a plain VPS work the same way — the app just needs
`DATABASE_URL` (pointing at Neon/Supabase) and the office-location env
vars, and to be served over HTTPS.

## Using it on mobile
Employees just open the deployed URL in their phone's browser (Chrome/Safari)
— no app install needed. First visit will prompt for **camera** and
**location** permission; both must be allowed. For a more native feel, they
can "Add to Home Screen" from the browser menu, which gives it an app icon.

> Camera and geolocation only work over **HTTPS** or `localhost` — this is a
> browser security rule, not something in this code. Render, Railway etc.
> give you HTTPS automatically.

## Local development

```bash
cd attendance_system
python -m venv venv && source venv/bin/activate     # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env      # fill in values if you want to test geofencing locally
python app.py
```
Open `http://localhost:5000`. (Geofencing and camera both work fine on
`localhost` without HTTPS — the browser makes an exception for it — but
once deployed, HTTPS is required.)

`face_recognition` needs **cmake** + a C++ toolchain to install (dlib
compiles from source):
- **Windows**: [VS Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) (C++ workload) + [CMake](https://cmake.org/download/)
- **macOS**: `brew install cmake`
- **Linux**: `sudo apt-get install cmake build-essential`

## Configuration reference (env vars)
| Variable | Purpose | Default |
|---|---|---|
| `DATABASE_URL` | Postgres connection string (use Neon/Supabase for permanence). Unset = local SQLite (not persistent on most hosts). | SQLite |
| `OFFICE_LAT` / `OFFICE_LNG` | Office coordinates for geofencing. Unset = geofence disabled. | disabled |
| `GEOFENCE_RADIUS_METERS` | Allowed distance from office, in meters. | 150 |
| `PORT` | Port to bind (set automatically by most hosts). | 5000 |

## Notes on accuracy & security
- `MATCH_TOLERANCE` in `app.py` (default `0.5`) controls face-match
  strictness. Good, even lighting matters more than this number.
- No liveness detection — a photo/video of someone could in principle fool
  it. Fine for internal, trust-based use; for higher-stakes deployments add
  a liveness SDK or require a blink/head-turn.
- GPS accuracy varies (typically 5–50m outdoors, worse indoors/high-rises)
  — set your radius with that margin in mind.
- Face encodings and location data are biometric/personal data — check
  what your local law requires (consent, disclosure, retention limits)
  before rolling this out, and restrict database access accordingly.
- The Register and Reports screens have no login in this version — anyone
  with the URL could register a face or view attendance. Add authentication
  (e.g. Flask-Login with an admin password) before real-world use if that's
  a concern.

## Project structure
```
attendance_system/
├── app.py              # Flask routes, face recognition, geofence check
├── database.py         # SQLAlchemy models (SQLite or cloud Postgres)
├── requirements.txt
├── Procfile             # process command for Render/Railway/Heroku-style hosts
├── render.yaml           # one-click Render Blueprint (web service + Postgres)
├── .env.example
├── templates/
│   └── index.html       # mobile-first UI (Scan / Register / Reports)
├── static/
│   ├── css/style.css
│   └── js/main.js       # camera capture + geolocation + API calls
└── instance/
    └── attendance.db    # local SQLite (only used when DATABASE_URL is unset)
```
