// comfy-colab-kit overlay — injected by comfy-gate into ComfyUI's index.html.
// Bottom-right pill: remaining time | App / Graph toggle | sleep. Also opens the profile's
// default workflow (in App mode when DEFAULT_VIEW=app) on first load of a session.
(() => {
  const css = `#kit-pill{position:fixed;right:calc(12px + env(safe-area-inset-right));bottom:calc(12px + env(safe-area-inset-bottom));z-index:99999;display:flex;gap:6px;align-items:center;
  max-width:calc(100vw - 24px);background:#211927;color:#F4F1F6;border:1px solid #33293b;border-radius:99px;padding:6px 8px;font:12px system-ui;box-shadow:0 4px 18px #0006}
  #kit-pill #kit-left{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  #kit-pill button{background:#33293b;color:#F4F1F6;border:0;border-radius:99px;min-height:32px;padding:0 10px;cursor:pointer;font:inherit;flex-shrink:0}
  #kit-pill button.on{background:linear-gradient(90deg,#F9AB00,#F2FF59);color:#211927;font-weight:700}
  #kit-pill .warn{color:#F9AB00}`;
  const st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);
  const pill = document.createElement("div"); pill.id = "kit-pill";
  pill.innerHTML = `<span id="kit-left">…</span><button id="kit-app">App</button><button id="kit-graph">Graph</button>
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
    if (!confirm("Sleep now and release the GPU?")) return;
    const r = await fetch("/_gate/stop", { method: "POST" }); if (r.ok) location.href = "/"; else alert((await r.json()).message);
  };
  q("kit-ext").onclick = async () => { const r = await fetch("/_gate/extend", { method: "POST" }); if (!r.ok) alert((await r.json()).message); poll(); };
  async function poll() {
    try {
      const s = await (await fetch("/_gate/status")).json();
      if (s.state !== "ready") { location.href = "/"; return; }
      const left = q("kit-left"); const r = s.remaining_s;
      left.textContent = r == null ? "" : `${Math.floor(r / 60)} min left`; left.className = s.warn ? "warn" : "";
      q("kit-ext").hidden = !(s.warn && s.can_extend && s.you?.owner);
      q("kit-stop").hidden = !(s.you?.owner || !s.owner_only.includes("stop"));
      paint();
    } catch (e) { /* transient */ }
  }
  async function openDefault() {
    if (sessionStorage.getItem("kit.opened")) return;
    const a = comfy(); if (!a?.loadGraphData) return setTimeout(openDefault, 500);
    sessionStorage.setItem("kit.opened", "1");
    try {
      const info = await (await fetch("/kit/info")).json();
      if (!info.default_workflow) return;
      const wf = await (await fetch(`/api/userdata/${encodeURIComponent("workflows/" + info.default_workflow)}`)).json();
      await a.loadGraphData(wf, true, true, info.default_workflow);
      if (info.default_view === "app") setTimeout(() => toggle(true), 300);
    } catch (e) { console.warn("[kit] default workflow", e); }
  }
  setInterval(poll, 30000); setTimeout(poll, 1500); setTimeout(openDefault, 1500);
})();
