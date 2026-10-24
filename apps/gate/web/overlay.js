// comfy-colab-kit overlay — injected by comfy-gate into ComfyUI's index.html.
// Bottom-right pill: remaining time | App / Graph toggle | sleep. Also opens the profile's
// default workflow (in App mode when DEFAULT_VIEW=app) on first load of a session.
(() => {
  const css = `#kit-pill{position:fixed;right:calc(16px + env(safe-area-inset-right));bottom:calc(16px + env(safe-area-inset-bottom));z-index:99999;display:flex;gap:8px;align-items:center;
  max-width:calc(100vw - 32px);background:rgba(24,20,29,.88);backdrop-filter:blur(16px) saturate(140%);-webkit-backdrop-filter:blur(16px) saturate(140%);color:#F4F1F6;
  border:1px solid rgba(255,255,255,.14);border-radius:99px;padding:6px 10px;font:500 12px/1 system-ui,-apple-system,sans-serif;box-shadow:0 8px 32px rgba(0,0,0,.55)}
  #kit-pill #kit-left{display:none;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;color:rgba(244,241,246,.75);padding:0 4px 0 2px;white-space:nowrap}
  #kit-pill button{background:rgba(255,255,255,.08);color:#F4F1F6;border:1px solid rgba(255,255,255,.08);border-radius:99px;min-height:32px;padding:0 12px;cursor:pointer;font:inherit;font-weight:500;
  display:inline-flex;align-items:center;justify-content:center;transition:all .15s ease;flex-shrink:0}
  #kit-pill button:hover{background:rgba(255,255,255,.15);border-color:rgba(255,255,255,.18)}
  #kit-pill button.on{background:linear-gradient(135deg,#F9AB00,#F2FF59);color:#120D18;font-weight:700;border-color:transparent;box-shadow:0 0 14px rgba(249,171,0,.35)}
  #kit-pill .warn{color:#F9AB00 !important;font-weight:700}`;
  const st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);
  const pill = document.createElement("div"); pill.id = "kit-pill";
  pill.innerHTML = `<span id="kit-left"></span><button id="kit-app">App</button><button id="kit-graph">Graph</button>
  <button id="kit-ext" hidden>+1h</button><button id="kit-stop" title="Sleep (release GPU)">⏻</button>`;
  const mount = () => document.body ? document.body.appendChild(pill) : setTimeout(mount, 100); mount();
  const q = (id) => document.getElementById(id);
  const comfy = () => window.app || window.comfyAPI?.app?.app;
  const linear = () => !!document.querySelector('[data-testid="linear-mode"], .linear-mode, [class*="linear"]');
  async function toggle(wantApp) {
    const a = comfy(); if (!a?.extensionManager?.command) return;
    try { if (wantApp !== linear()) await a.extensionManager.command.execute("Comfy.ToggleLinear"); } catch (e) { console.warn("[kit] toggle", e); }
    paint();
  }
  function paint() { q("kit-app").classList.toggle("on", linear()); q("kit-graph").classList.toggle("on", !linear()); }
  q("kit-app").onclick = () => toggle(true); q("kit-graph").onclick = () => toggle(false);
  q("kit-stop").onclick = async () => {
    if (q("kit-stop").disabled) return;
    if (!confirm("Sleep now and release the GPU?")) return;
    const r = await fetch("/_gate/stop", { method: "POST" }); if (r.ok) location.href = "/"; else alert((await r.json()).message);
  };
  q("kit-ext").onclick = async () => { const r = await fetch("/_gate/extend", { method: "POST" }); if (!r.ok) alert((await r.json()).message); poll(); };
  async function poll() {
    try {
      const s = await (await fetch("/_gate/status")).json();
      if (s.state !== "ready") { location.href = "/"; return; }
      const left = q("kit-left"); const r = s.remaining_s;
      if (r != null) {
        left.style.display = "inline-flex";
        left.textContent = `${Math.floor(r / 60)} min left`;
        left.className = s.warn ? "warn" : "";
      } else {
        left.style.display = "none";
      }
      q("kit-ext").hidden = !(s.warn && s.can_extend && s.you?.owner);
      const canStop = s.can_stop !== false;
      const stopBtn = q("kit-stop");
      stopBtn.disabled = !canStop;
      if (!canStop) {
        stopBtn.style.opacity = "0.38";
        stopBtn.style.cursor = "not-allowed";
        stopBtn.title = "Only initiator can stop (仅开机者可释放 GPU)";
      } else {
        stopBtn.style.opacity = "";
        stopBtn.style.cursor = "pointer";
        stopBtn.title = "Sleep (release GPU)";
      }
      stopBtn.hidden = false;
      paint();
    } catch (e) { /* transient */ }
  }
  async function openDefault() {
    const a = comfy(); if (!a?.loadGraphData) return setTimeout(openDefault, 500);
    try {
      const info = await (await fetch("/kit/info")).json();
      if (!info.default_workflow) return;
      if (sessionStorage.getItem("kit.loaded_wf") === info.default_workflow) return;
      let res = await fetch("/kit/workflow");
      if (!res.ok) res = await fetch("/kit/workflows/" + encodeURIComponent(info.default_workflow));
      if (!res.ok) res = await fetch("/default_workflow");
      if (!res.ok) res = await fetch("/api/userdata/workflows/" + encodeURIComponent(info.default_workflow));
      if (!res.ok) res = await fetch(`/api/userdata/${encodeURIComponent("workflows/" + info.default_workflow)}`);
      if (!res.ok) return;
      const wf = await res.json();
      await a.loadGraphData(wf, true, true, info.default_workflow);
      sessionStorage.setItem("kit.loaded_wf", info.default_workflow);
      if (info.default_view === "app") setTimeout(() => toggle(true), 300);
    } catch (e) { console.warn("[kit] default workflow", e); }
  }
  setInterval(poll, 30000); setTimeout(poll, 1500); setTimeout(openDefault, 1500);
})();
