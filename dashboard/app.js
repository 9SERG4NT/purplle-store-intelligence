// Live dashboard: polls the Store Intelligence API and re-renders every 1.5s.
const STORE = new URLSearchParams(location.search).get("store") || "STORE_BLR_002";
const API = ""; // same origin as the mounted dashboard
let prevVisitors = null;

const $ = (id) => document.getElementById(id);
const pct = (x) => (x == null ? "—" : (x * 100).toFixed(1) + "%");

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
    $("conv").textContent = pct(m.conversion_rate);
    $("convd").textContent = `${m.converted_visitors}/${m.unique_visitors} converted · ${m.purchases} purchases`;
    $("visitors").textContent = m.unique_visitors;
    if (prevVisitors !== null && m.unique_visitors !== prevVisitors) flash($("visitors"));
    prevVisitors = m.unique_visitors;
    $("staffd").textContent = `${m.staff_excluded} staff excluded · confidence ${m.data_confidence}`;
    $("queue").textContent = m.current_queue_depth;
    $("queued").textContent = `peak ${m.max_queue_depth}`;
    $("abandon").textContent = pct(m.abandonment_rate);
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

// ---- processed-footage replay (annotated MP4s rendered by pipeline/annotate.py) ----
const camSel = document.getElementById("camSel");
const camVideo = document.getElementById("camVideo");
const vidHint = document.getElementById("vidHint");
let _vidTimer = null;
function loadCam() {
  vidHint.hidden = true;                       // never show while (re)loading
  camVideo.src = `media/${camSel.value}.mp4`;
  camVideo.load();
  camVideo.play().catch(() => {});
  clearTimeout(_vidTimer);
  // only surface the hint if, after 6s, the video genuinely hasn't loaded a frame
  _vidTimer = setTimeout(() => { if (camVideo.readyState === 0) vidHint.hidden = false; }, 6000);
}
// any sign of playback hides the hint for good
["loadeddata", "canplay", "playing", "timeupdate"].forEach((ev) =>
  camVideo.addEventListener(ev, () => { vidHint.hidden = true; clearTimeout(_vidTimer); }));
camSel.addEventListener("change", loadCam);
loadCam();

// ---- live replay button: reset the store + re-stream so KPIs climb from zero ----
const replayBtn = document.getElementById("replayBtn");
async function pollReplay() {
  try {
    const s = await getJSON("/demo/replay/status");
    if (s.running) {
      replayBtn.disabled = true;
      replayBtn.textContent = `▶ streaming ${s.sent}/${s.total}`;
      setTimeout(pollReplay, 700);
    } else {
      replayBtn.disabled = false;
      replayBtn.textContent = "▶ Replay live";
    }
  } catch { replayBtn.disabled = false; replayBtn.textContent = "▶ Replay live"; }
}
replayBtn.addEventListener("click", async () => {
  replayBtn.disabled = true; replayBtn.textContent = "▶ starting…";
  try {
    await fetch(`${API}/demo/replay?store_id=${STORE}&seconds=20`, { method: "POST" });
    pollReplay();
  } catch { replayBtn.disabled = false; replayBtn.textContent = "▶ Replay live"; }
});

tick();
setInterval(tick, 1500);
