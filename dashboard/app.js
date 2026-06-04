// Live dashboard: polls the Store Intelligence API and re-renders every 1.5s.
// Two stores are wired in; each has its own annotated replay cameras (media/*.mp4
// + a media/*.json sidecar of per-frame counts that drives the live readout).
const STORES = {
  STORE_BLR_002: {
    label: "STORE_BLR_002 — Brigade Road, Bangalore",
    cams: [
      { file: "cam_entry_01", cam: "CAM 3", name: "Entry / exit", role: "entry" },
      { file: "cam_floor_01", cam: "CAM 1", name: "Floor · skincare", role: "floor" },
      { file: "cam_floor_02", cam: "CAM 2", name: "Floor · makeup", role: "floor" },
      { file: "cam_billing_01", cam: "CAM 5", name: "Billing", role: "billing" },
      { file: "cam_back_01", cam: "CAM 4", name: "Back room", role: "backroom" },
    ],
  },
  STORE_BLR_009: {
    label: "STORE_BLR_009 — Second store (extra clips)",
    cams: [
      { file: "cam_entry_a", cam: "Entry 1", name: "Entry / exit", role: "entry" },
      { file: "cam_entry_b", cam: "Entry 2", name: "Entry / exit", role: "entry" },
      { file: "cam_billing_a", cam: "Billing", name: "Checkout", role: "billing" },
      { file: "cam_floor_a", cam: "Zone", name: "Floor · shelf aisle", role: "floor" },
    ],
  },
};

// Per-role view: which icon, what the camera does, which live stats to show, and
// which store-wide metric it feeds — so each camera reads as its own mini-dashboard.
const ROLE_INFO = {
  entry: {
    icon: "\u{1F6AA}", title: "Entry / exit",
    stats: [["entries", "Entries in", true], ["exits", "Exits out", false], ["persons", "On screen", false]],
    blurb: "Counts people crossing the door <b>tripwire</b> (red line, IN arrow = into the store). Inbound crossings feed <b>Unique visitors</b> and the top of the funnel.",
  },
  floor: {
    icon: "\u{1F6CD}", title: "Sales floor",
    stats: [["persons", "On screen", true]],
    blurb: "Tracks browsing inside the product zones. Time spent here drives the <b>Zone heatmap</b> and the &ldquo;Browsed a zone&rdquo; funnel step.",
  },
  billing: {
    icon: "\u{1F4B3}", title: "Billing counter",
    stats: [["queue", "In queue now", true], ["persons", "On screen", false]],
    blurb: "Watches the checkout queue (orange box). Queue depth feeds the <b>Queue</b> KPI and &ldquo;Reached billing&rdquo;; people who leave the line feed <b>Abandonment</b>.",
  },
  backroom: {
    icon: "\u{1F4E6}", title: "Back room",
    stats: [["persons", "On screen", true]],
    blurb: "Stock area, not customer-facing. Anyone detected here is treated as <b>staff</b> and excluded from visitor counts.",
  },
};
const STORE = STORES[new URLSearchParams(location.search).get("store")]
  ? new URLSearchParams(location.search).get("store") : "STORE_BLR_002";
const API = ""; // same origin as the mounted dashboard

// ---- store switcher: rebuild on change, reload with ?store= ----
const storeSel = document.getElementById("storeSel");
if (storeSel) {
  storeSel.innerHTML = Object.entries(STORES)
    .map(([id, s]) => `<option value="${id}" ${id === STORE ? "selected" : ""}>${id}</option>`).join("");
  storeSel.addEventListener("change", () => {
    location.search = "?store=" + encodeURIComponent(storeSel.value);
  });
}
const storeLineEl = document.getElementById("storeLine");
if (storeLineEl && STORES[STORE]) storeLineEl.textContent = STORES[STORE].label;
let prevVisitors = null;

const $ = (id) => document.getElementById(id);
const pct = (x) => (x == null ? "—" : (x * 100).toFixed(1) + "%");
const _prev = {};
function setKPI(id, text, val) {
  const el = $(id);
  if (_prev[id] !== undefined && _prev[id] !== val) flash(el);
  _prev[id] = val;
  el.textContent = text;
}

async function getJSON(path) {
  const r = await fetch(API + path, { headers: { "Cache-Control": "no-cache" } });
  if (!r.ok) throw new Error(path + " -> " + r.status);
  return r.json();
}

function flash(el) { el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash"); }

const STAGE_LABEL = {
  entered: "Entered store", browsed_zone: "Browsed a zone",
  billing_queue: "Reached billing", purchased: "Purchased",
};
function renderFunnel(f) {
  const box = $("funnel");
  if (!f.stages || !f.stages.length) { box.innerHTML = '<div class="empty">No sessions yet.</div>'; return; }
  const max = Math.max(...f.stages.map((s) => s.count), 1);
  box.innerHTML = f.stages.map((s) => {
    const w = (100 * s.count) / max;
    const drop = s.dropoff_pct_from_prev == null ? "" :
      `<span class="drop">▼ ${s.dropoff_pct_from_prev}% drop</span>`;
    return `<div class="funnel-stage">
      <div class="top"><span>${STAGE_LABEL[s.stage] || s.stage.replace(/_/g, " ")}</span>
      <span>${s.count} ${drop}</span></div>
      <div class="bar"><span style="width:${w}%"></span></div></div>`;
  }).join("") + `<div class="delta" style="color:var(--accent-ink);margin-top:10px;font-weight:600">
      Overall conversion: ${f.overall_conversion_pct}%</div>`;
}

function renderHeatmap(h) {
  const box = $("heatmap");
  if (!h.cells || !h.cells.length) { box.innerHTML = '<div class="empty">No zone data.</div>'; return; }
  const conf = h.data_confidence === "OK" ? "" :
    `<div class="delta" style="margin-bottom:10px">⚠ data_confidence: ${h.data_confidence} (${h.sessions_in_window} sessions)</div>`;
  box.innerHTML = conf + h.cells.map((c) =>
    `<div class="zone"><div class="name">${c.zone_id}</div>
      <div class="hb"><span style="width:${c.score}%"></span></div>
      <div class="v">${c.visits}</div></div>`).join("");
}

function renderAnomalies(a) {
  const box = $("anomalies");
  if (!a.anomalies || !a.anomalies.length) {
    box.innerHTML = '<div class="empty">✓ No active anomalies.</div>'; return;
  }
  box.innerHTML = a.anomalies.map((x) =>
    `<div class="anom ${x.severity}"><div class="t">${x.severity} · ${x.type}</div>
      <div>${x.message}</div><div class="a">→ ${x.suggested_action}</div></div>`).join("");
}

function renderHealth(hp) {
  const box = $("health");
  const s = (hp.stores || []).find((x) => x.store_id === STORE) || (hp.stores || [])[0];
  const pill = $("feedPill");
  if (s && s.stale_feed) { pill.textContent = "STALE FEED"; pill.className = "pill stale"; }
  else if (s) { pill.textContent = "● LIVE"; pill.className = "pill live"; }
  else { pill.textContent = "no data"; pill.className = "pill"; }
  box.innerHTML = `
    <div class="delta">DB connected: <b>${hp.db_connected}</b> · status: <b>${hp.status}</b></div>
    <div class="delta">Last event: ${s ? s.last_event_ts : "—"}</div>
    <div class="delta">Last ingest: ${s ? s.last_ingest_ts : "—"}</div>
    <div class="delta">Events stored: ${s ? s.event_count : 0} · lag: ${s && s.lag_seconds != null ? s.lag_seconds + "s" : "—"}</div>`;
}

async function tick() {
  try {
    const [m, f, h, a, hp] = await Promise.all([
      getJSON(`/stores/${STORE}/metrics`),
      getJSON(`/stores/${STORE}/funnel`),
      getJSON(`/stores/${STORE}/heatmap`),
      getJSON(`/stores/${STORE}/anomalies`),
      getJSON(`/health`),
    ]);
    setKPI("conv", pct(m.conversion_rate), m.conversion_rate);
    $("convd").textContent = `${m.converted_visitors}/${m.unique_visitors} converted · ${m.purchases} purchases`;
    setKPI("visitors", m.unique_visitors, m.unique_visitors);
    $("staffd").textContent = `${m.staff_excluded} staff excluded · confidence ${m.data_confidence}`;
    setKPI("queue", m.current_queue_depth, m.current_queue_depth);
    $("queued").textContent = `peak ${m.max_queue_depth}`;
    setKPI("abandon", pct(m.abandonment_rate), m.abandonment_rate);
    $("purch").textContent = `${m.purchases} POS txns today`;
    renderFunnel(f); renderHeatmap(h); renderAnomalies(a); renderHealth(hp);
    $("updated").textContent = "updated " + new Date().toLocaleTimeString();
    if (m.window_start) {
      const d = new Date(m.window_start);
      $("windowLine").textContent = "data day: " + d.toISOString().slice(0, 10);
    }
  } catch (e) {
    $("feedPill").textContent = "API unreachable"; $("feedPill").className = "pill stale";
    $("updated").textContent = String(e);
  }
}

// ---- camera view: annotated MP4 + a live readout that ticks with the playhead ----
// Each MP4 has a sibling media/<cam>.json sidecar (rendered by pipeline/annotate.py):
// per-output-frame running counts. We map video.currentTime -> frame index and mirror
// that frame's counts as HTML, so the dashboard is dynamic in lock-step with the video.
const camTabs = document.getElementById("camTabs");
const camVideo = document.getElementById("camVideo");
const vidHint = document.getElementById("vidHint");
const camStrip = document.getElementById("camStrip");
const camBlurb = document.getElementById("camBlurb");
const camTitle = document.getElementById("camTitle");
const camClock = document.getElementById("camClock");
const CAMS = STORES[STORE] ? STORES[STORE].cams : [];
let activeCam = 0;
let camStats = null;  // sidecar for the active camera, or null if not rendered yet

function renderCamReadout(c, sample) {
  const info = ROLE_INFO[c.role] || ROLE_INFO.floor;
  camStrip.innerHTML = info.stats.map(([key, label, accent]) => {
    const v = sample ? sample[key] : null;
    return `<div class="cstat"><div class="cv ${accent ? "accent" : ""}">${v == null ? "—" : v}</div>
      <div class="cl">${label}</div></div>`;
  }).join("");
  let blurb = info.blurb;
  if (camStats && camStats.zones && camStats.zones.length && c.role === "floor") {
    blurb += ` &middot; in view: <b>${camStats.zones.join(", ")}</b>`;
  }
  camBlurb.innerHTML = blurb;
  camClock.textContent = sample && sample.clock ? sample.clock : "";
}

function tickCamReadout() {
  if (!camStats || !camStats.samples || !camStats.samples.length) return;
  const fps = camStats.fps || 6;
  let i = Math.round(camVideo.currentTime * fps);
  i = Math.max(0, Math.min(camStats.samples.length - 1, i));
  renderCamReadout(CAMS[activeCam], camStats.samples[i]);
}

async function loadCam(i) {
  activeCam = i;
  const c = CAMS[i];
  const info = ROLE_INFO[c.role] || ROLE_INFO.floor;
  camTitle.textContent = `${info.icon} ${c.cam} · ${info.title}`;
  [...camTabs.children].forEach((b, j) => b.classList.toggle("active", j === i));
  vidHint.hidden = true;
  camVideo.src = `media/${c.file}.mp4`;
  camVideo.load();
  camVideo.play().catch(() => {});
  camStats = null;
  renderCamReadout(c, null);          // labels now, values fill once the sidecar loads
  try { camStats = await getJSON(`media/${c.file}.json`); } catch { camStats = null; }
  tickCamReadout();
}

// build the tab bar (one button per camera, labelled by what it watches)
camTabs.innerHTML = CAMS.map((c, i) => {
  const info = ROLE_INFO[c.role] || ROLE_INFO.floor;
  return `<button class="camtab ${i === 0 ? "active" : ""}" data-i="${i}">
    <span class="ci">${info.icon}</span>${c.cam} · ${c.name}</button>`;
}).join("");
camTabs.addEventListener("click", (e) => {
  const b = e.target.closest(".camtab");
  if (b) loadCam(Number(b.dataset.i));
});

camVideo.addEventListener("timeupdate", tickCamReadout);
// Show the "render replays" hint only on a genuine load failure; any successful load hides it.
camVideo.addEventListener("error", () => { vidHint.hidden = false; });
["loadedmetadata", "loadeddata", "canplay", "playing"].forEach((ev) =>
  camVideo.addEventListener(ev, () => { vidHint.hidden = true; }));
if (CAMS.length) loadCam(0);

// ---- live replay: reset the store + re-stream so KPIs climb from zero ----
const replayBtn = document.getElementById("replayBtn");
const loopChk = document.getElementById("loopChk");
async function startReplay() {
  try {
    await fetch(`${API}/demo/replay?store_id=${STORE}&seconds=18`, { method: "POST" });
    pollReplay();
  } catch { replayBtn.disabled = false; replayBtn.textContent = "▶ Replay live"; }
}
async function pollReplay() {
  try {
    const s = await getJSON("/demo/replay/status");
    if (s.running) {
      replayBtn.disabled = true;
      replayBtn.textContent = `● streaming ${s.sent}/${s.total}`;
      setTimeout(pollReplay, 600);
    } else {
      replayBtn.disabled = false;
      replayBtn.textContent = "▶ Replay live";
      if (loopChk && loopChk.checked) setTimeout(startReplay, 3000);  // continuous live loop
    }
  } catch { replayBtn.disabled = false; replayBtn.textContent = "▶ Replay live"; }
}
replayBtn.addEventListener("click", () => { replayBtn.disabled = true; replayBtn.textContent = "● starting…"; startReplay(); });

tick();
setInterval(tick, 1500);
// auto-play once on load so the dashboard is dynamic immediately (KPIs animate from 0)
setTimeout(() => { replayBtn.disabled = true; replayBtn.textContent = "● starting…"; startReplay(); }, 1200);
