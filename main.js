// ---------- Tab switching ----------
const tabButtons = document.querySelectorAll(".tab-btn");
const tabPanels = document.querySelectorAll(".tab-panel");

function activateTab(tabName) {
  tabPanels.forEach(p => p.classList.add("hidden"));
  tabButtons.forEach(b => b.classList.remove("active"));
  document.getElementById(`tab-${tabName}`).classList.remove("hidden");
  document.querySelector(`.tab-btn[data-tab="${tabName}"]`).classList.add("active");

  if (tabName === "scan") { startCamera(scanVideo); refreshLocation(); }
  if (tabName === "register") { startCamera(regVideo); loadEmployees(); }
  if (tabName === "reports") loadAttendance();
}

tabButtons.forEach(btn => btn.addEventListener("click", () => activateTab(btn.dataset.tab)));

// ---------- Camera helpers ----------
const scanVideo = document.getElementById("scan-video");
const scanCanvas = document.getElementById("scan-canvas");
const regVideo = document.getElementById("reg-video");
const regCanvas = document.getElementById("reg-canvas");

async function startCamera(videoEl) {
  try {
    if (videoEl.srcObject) return;
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "user", width: { ideal: 480 }, height: { ideal: 360 } }
    });
    videoEl.srcObject = stream;
  } catch (err) {
    console.error("Camera error:", err);
    alert("Could not access the camera. Please allow camera permissions.");
  }
}

function captureFrame(videoEl, canvasEl) {
  canvasEl.width = videoEl.videoWidth || 480;
  canvasEl.height = videoEl.videoHeight || 360;
  const ctx = canvasEl.getContext("2d");
  ctx.drawImage(videoEl, 0, 0, canvasEl.width, canvasEl.height);
  return canvasEl.toDataURL("image/jpeg", 0.85);
}

// ---------- Geolocation ----------
let currentPosition = null; // {lat, lng}
const geoStatusEl = document.getElementById("geo-status");

function refreshLocation() {
  if (!navigator.geolocation) {
    geoStatusEl.textContent = "Geolocation not supported on this device.";
    return;
  }
  geoStatusEl.textContent = "Checking location…";
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      currentPosition = { lat: pos.coords.latitude, lng: pos.coords.longitude };
      geoStatusEl.textContent = `Location detected (±${Math.round(pos.coords.accuracy)}m accuracy)`;
    },
    (err) => {
      currentPosition = null;
      geoStatusEl.textContent = "Location permission denied — check-in/out may be blocked.";
      console.error("Geolocation error:", err);
    },
    { enableHighAccuracy: true, timeout: 10000 }
  );
}

// ---------- Scan tab ----------
const scanResult = document.getElementById("scan-result");

async function doScan(action) {
  scanResult.innerHTML = "Scanning...";
  const image = captureFrame(scanVideo, scanCanvas);

  const body = { image, action };
  if (currentPosition) {
    body.lat = currentPosition.lat;
    body.lng = currentPosition.lng;
  }

  try {
    const res = await fetch("/api/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();

    if (!res.ok || !data.matched) {
      scanResult.innerHTML = `<div class="result-error">${data.error || data.message || "Not recognized"}</div>`;
      return;
    }

    const resultLabels = {
      checked_in: "Checked in",
      checked_out: "Checked out",
      already_checked_in: "Already checked in today",
      already_checked_out: "Already checked out today",
      not_checked_in: "Not checked in yet today",
    };

    scanResult.innerHTML = `
      <div class="text-center">
        <div class="result-success text-lg">${data.name}</div>
        <div class="text-sm text-slate-500">${data.employee_code}</div>
        <div class="mt-2">${resultLabels[data.result] || data.result}</div>
        <div class="text-xs text-slate-400 mt-1">Match confidence: ${data.confidence}%</div>
      </div>`;
  } catch (err) {
    scanResult.innerHTML = `<div class="result-error">Network error — is the server running?</div>`;
  }
}

document.getElementById("btn-check-in").addEventListener("click", () => doScan("check_in"));
document.getElementById("btn-check-out").addEventListener("click", () => doScan("check_out"));

// ---------- Register tab ----------
let capturedImage = null;

document.getElementById("btn-capture").addEventListener("click", () => {
  capturedImage = captureFrame(regVideo, regCanvas);
  document.getElementById("reg-preview").textContent = "Face captured. Ready to register.";
});

document.getElementById("register-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = document.getElementById("reg-name").value.trim();
  const employee_code = document.getElementById("reg-code").value.trim();
  const msgEl = document.getElementById("reg-message");

  if (!capturedImage) {
    msgEl.innerHTML = `<span class="result-error">Please capture a face photo first.</span>`;
    return;
  }

  msgEl.innerHTML = "Registering...";
  try {
    const res = await fetch("/api/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, employee_code, image: capturedImage }),
    });
    const data = await res.json();

    if (!res.ok) {
      msgEl.innerHTML = `<span class="result-error">${data.error}</span>`;
      return;
    }

    msgEl.innerHTML = `<span class="result-success">Registered ${data.name} (${data.employee_code})</span>`;
    document.getElementById("register-form").reset();
    capturedImage = null;
    document.getElementById("reg-preview").textContent = "No face captured yet.";
    loadEmployees();
  } catch (err) {
    msgEl.innerHTML = `<span class="result-error">Network error — is the server running?</span>`;
  }
});

async function loadEmployees() {
  const res = await fetch("/api/employees");
  const employees = await res.json();
  const list = document.getElementById("employee-list");
  list.innerHTML = "";
  employees.forEach(emp => {
    const row = document.createElement("div");
    row.className = "flex items-center justify-between py-2";
    row.innerHTML = `
      <div>
        <div class="font-medium">${emp.name}</div>
        <div class="text-xs text-slate-500">${emp.employee_code}</div>
      </div>
      <button class="text-red-600 text-xs" onclick="deleteEmployee(${emp.id})">Remove</button>
    `;
    list.appendChild(row);
  });
}

async function deleteEmployee(id) {
  if (!confirm("Remove this employee and their attendance history?")) return;
  await fetch(`/api/employees/${id}`, { method: "DELETE" });
  loadEmployees();
}

// ---------- Reports tab ----------
async function loadAttendance(dateFilter) {
  const url = dateFilter ? `/api/attendance?date=${dateFilter}` : "/api/attendance";
  const res = await fetch(url);
  const records = await res.json();
  const list = document.getElementById("attendance-list");
  list.innerHTML = "";
  records.forEach(r => {
    const row = document.createElement("div");
    row.className = "py-2";
    row.innerHTML = `
      <div class="flex justify-between">
        <span class="font-medium">${r.name}</span>
        <span class="text-xs text-slate-500">${r.date}</span>
      </div>
      <div class="text-xs text-slate-500">${r.employee_code} · In: ${r.check_in_time || "—"} · Out: ${r.check_out_time || "—"}</div>
    `;
    list.appendChild(row);
  });

  const exportBtn = document.getElementById("btn-export");
  exportBtn.href = dateFilter ? `/api/export?date=${dateFilter}` : "/api/export";
}

document.getElementById("btn-filter").addEventListener("click", () => {
  const d = document.getElementById("report-date").value;
  if (d) loadAttendance(d);
});
document.getElementById("btn-clear-filter").addEventListener("click", () => {
  document.getElementById("report-date").value = "";
  loadAttendance();
});

// ---------- Init ----------
activateTab("scan");
