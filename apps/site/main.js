const REPO_URL = "https://github.com/FireTable/comfy-colab-kit";

// Animated aurora background — vanilla WebGL port inspired by React Bits "Aurora"
// (https://reactbits.dev, MIT License, (c) David Haz). Falls back to CSS gradient without WebGL
// and renders a single static frame under prefers-reduced-motion.
(() => {
  const c = document.getElementById("bg"); if (!c) return;
  const gl = c.getContext("webgl", { premultipliedAlpha: true, alpha: true, antialias: false });
  if (!gl) { c.remove(); return; }
  const vs = "attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}";
  const fs = `precision highp float;uniform float t;uniform vec2 r;uniform vec3 c0,c1,c2;uniform float amp;
  vec3 perm(vec3 x){return mod(((x*34.)+1.)*x,289.);}
  float sn(vec2 v){const vec4 C=vec4(.211324865,.366025404,-.577350269,.024390244);vec2 i=floor(v+dot(v,C.yy));
   vec2 x0=v-i+dot(i,C.xx);vec2 i1=x0.x>x0.y?vec2(1.,0.):vec2(0.,1.);vec4 x12=x0.xyxy+C.xxzz;x12.xy-=i1;i=mod(i,289.);
   vec3 p=perm(perm(i.y+vec3(0.,i1.y,1.))+i.x+vec3(0.,i1.x,1.));vec3 m=max(.5-vec3(dot(x0,x0),dot(x12.xy,x12.xy),dot(x12.zw,x12.zw)),0.);
   m=m*m;m=m*m;vec3 x=2.*fract(p*C.www)-1.;vec3 h=abs(x)-.5;vec3 ox=floor(x+.5);vec3 a0=x-ox;
   m*=1.79284291-.85373472*(a0*a0+h*h);vec3 g;g.x=a0.x*x0.x+h.x*x0.y;g.yz=a0.yz*x12.xz+h.yz*x12.yw;return 130.*dot(m,g);}
  void main(){vec2 uv=gl_FragCoord.xy/r;vec3 ramp=uv.x<.5?mix(c0,c1,uv.x*2.):mix(c1,c2,(uv.x-.5)*2.);
   float h=sn(vec2(uv.x*2.+t*.1,t*.25))*.5*amp;h=exp(h);h=(uv.y*2.-h+.2);float i=.6*h;
   float a=smoothstep(.2-.5,.2+.5,i);vec3 col=i*ramp;
   float intensity = (c0.r > 0.9 && c1.r > 0.9) ? 0.35 : 0.55;
   gl_FragColor=vec4(col*a,a)*intensity;}`;
  const sh = (ty, s) => { const o = gl.createShader(ty); gl.shaderSource(o, s); gl.compileShader(o); return o; };
  const pr = gl.createProgram(); gl.attachShader(pr, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(pr, sh(gl.FRAGMENT_SHADER, fs));
  gl.linkProgram(pr); if (!gl.getProgramParameter(pr, gl.LINK_STATUS)) { c.remove(); return; } gl.useProgram(pr);
  const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1,3,-1,-1,3]), gl.STATIC_DRAW);
  const lp = gl.getAttribLocation(pr, "p"); gl.enableVertexAttribArray(lp); gl.vertexAttribPointer(lp, 2, gl.FLOAT, false, 0, 0);
  const U = n => gl.getUniformLocation(pr, n), hex = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16) / 255);
  const pal = () => { const cs = getComputedStyle(document.documentElement);
    ["c0", "c1", "c2"].forEach((k, i) => gl.uniform3fv(U(k), hex(cs.getPropertyValue("--a" + i).trim() || "#F9AB00"))); };
  let amp = 1; window.kitBg = { set(o) { if (o.amp != null) amp = o.amp; pal(); } };
  const rm = matchMedia("(prefers-reduced-motion: reduce)");
  const size = () => { const d = Math.min(devicePixelRatio || 1, 1.5) * .5; c.width = innerWidth * d; c.height = innerHeight * d;
    gl.viewport(0, 0, c.width, c.height); gl.uniform2f(U("r"), c.width, c.height); };
  addEventListener("resize", () => { size(); if (rm.matches) frame(8000); }); size(); pal();
  const frame = ms => { gl.uniform1f(U("t"), ms * .001); gl.uniform1f(U("amp"), amp); gl.clearColor(0,0,0,0); gl.clear(gl.COLOR_BUFFER_BIT); gl.drawArrays(gl.TRIANGLES, 0, 3); };
  const loop = ms => { if (!document.hidden) frame(ms); if (!rm.matches) requestAnimationFrame(loop); };
  if (rm.matches) frame(8000); else requestAnimationFrame(loop);
  rm.addEventListener?.("change", () => { if (!rm.matches) requestAnimationFrame(loop); });
})();

// Spotlight card cursor tracking — cleanly disappears on mouse leave
document.querySelectorAll(".card").forEach((card) => {
  card.addEventListener("pointermove", (e) => {
    const r = card.getBoundingClientRect();
    card.style.setProperty("--mx", (e.clientX - r.left) + "px");
    card.style.setProperty("--my", (e.clientY - r.top) + "px");
  });
});

// Theme management
const root = document.documentElement;
const themeBtn = document.getElementById("theme-btn");
if (themeBtn) {
  themeBtn.onclick = () => {
    const next = root.dataset.theme === "dark" ? "light" : "dark";
    root.dataset.theme = next;
    localStorage.setItem("kit.theme", next);
    if (window.kitBg) window.kitBg.set({});
  };
}
window.matchMedia?.("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
  if (!localStorage.getItem("kit.theme")) {
    const next = e.matches ? "dark" : "light";
    root.dataset.theme = next;
    if (window.kitBg) window.kitBg.set({});
  }
});

// Internationalization (i18n) Dictionary
const I18N = {
  en: {
    metaTitle: "Comfy Colab Kit — Remote, Wake-on-Demand ComfyUI on Google Colab",
    metaDesc: "Wake ComfyUI on Google Colab GPUs on-demand from your phone, browser, or autonomous AI agent. Protected by Cloudflare Access zero-trust auth.",
    "nav.problem": "Problem",
    "nav.how": "How It Works",
    "nav.comp": "Comparison",
    "nav.features": "Features",
    "nav.profiles": "Profiles",
    "nav.numbers": "Benchmarks",
    "nav.quickstart": "Quick Start",
    "nav.faq": "FAQ",
    "hero.badge": "Wake-on-demand ComfyUI",
    "hero.stateSleep": "Sleeping",
    "hero.stateWake": "Waking",
    "hero.stateReady": "Ready",
    "hero.title": "ComfyUI on Colab GPUs, awake only when you need it",
    "hero.sub": "Break free from baby-sitting Colab browser tabs. Comfy Colab Kit lets you wake ComfyUI on demand on Google Colab L4 GPUs from your phone, laptop, or autonomous AI agent. Protected by your personal Cloudflare Access zero-trust gate with automated idle shutdown.",
    "hero.btnPrimary": "Quick Start",
    "hero.btnGitHub": "GitHub",
    "hero.stat": "L4 · ~1.54 CU/h · 28.4 s per 1024² image",
    "problem.badge": "Pain Points",
    "problem.titlePrefix": "The",
    "problem.titleShiny": "Problem",
    "problem.lead": "Dedicated cloud GPUs drain your budget 24/7, while raw Colab notebooks force you to sit at a PC and baby-sit fragile browser tabs.",
    "problem.c1Tag": "Idle Waste",
    "problem.c1Title": "Cloud GPUs bill continuously",
    "problem.c1Desc": "A rented cloud GPU bills around the clock, even when hours pass without generating a single image.",
    "problem.c2Tag": "Tethered to PC",
    "problem.c2Title": "Colab notebooks are fragile",
    "problem.c2Desc": "Notebooks time out, public URLs change randomly, laptop sleep kills the session, and manual dependency setups waste time.",
    "problem.c3Tag": "Collaboration & Agents",
    "problem.c3Title": "Sharing & automation are messy",
    "problem.c3Desc": "Teammates accidentally spin up duplicate VMs, and headless AI agents have no clean way to trigger or orchestrate generation.",
    "problem.closing": "Comfy Colab Kit gives Colab a single, stable front door that sleeps when idle and wakes in one click from anywhere.",
    "how.badge": "Architecture",
    "how.titlePrefix": "How It",
    "how.titleShiny": "Works",
    "how.lead": "A lean, zero-trust bridge connecting your personal devices and AI agents with on-demand Colab hardware.",
    "flow.browserTitle": "Browser / Agent",
    "flow.browserDesc": "mobile, web or API",
    "flow.cfDesc": "zero-trust auth",
    "flow.gateTitle": "VPS Gate",
    "flow.gateDesc": "Caddy + comfy-gate",
    "flow.colabDesc": "ComfyUI runtime",
    "how.s1": "Open your URL or call the API. Cloudflare Access verifies identity before anything touches your infrastructure.",
    "how.s2": "If the GPU VM is asleep, the gate presents a sleek wake card (or handles agent API requests). Click \"Wake\" to begin.",
    "how.s3": "The gate automatically allocates a Colab L4, sets up ComfyUI, and syncs models. Everyone watching sees identical live progress.",
    "how.s4": "Once ready, the same URL seamlessly proxies the authentic ComfyUI web application. Idle timeouts or budget caps return it safely to sleep.",
    "how.demoLabel": "Waking… downloading models",
    "comp.badge": "The Paradigm Shift",
    "comp.titlePrefix": "Why",
    "comp.titleShiny": "Comfy Colab Kit",
    "comp.lead": "Compare the daily friction of raw Colab notebooks with our zero-trust, wake-on-demand architecture.",
    "comp.legacyBadge": "The Old Way",
    "comp.legacyTitle": "Traditional Colab Notebooks",
    "comp.l1Title": "Tethered to PC & Browser",
    "comp.l1Desc": "Must sit in front of a computer, manually run notebook cells, and baby-sit the browser tab.",
    "comp.l2Title": "Ephemeral & Fragile Sessions",
    "comp.l2Desc": "Laptop sleep or network blips kill the session. URLs change unpredictably on every restart.",
    "comp.l3Title": "Zero Access Control",
    "comp.l3Desc": "Either unauthenticated public tunnels or sharing your private Google account credentials.",
    "comp.l4Title": "Inaccessible to AI Agents",
    "comp.l4Desc": "Headless AI coding agents cannot trigger or orchestrate workflows without human manual setup.",
    "comp.l5Title": "Overnight Compute Waste",
    "comp.l5Desc": "Forgetting to terminate notebooks burns through dozens of costly Compute Units (CU).",
    "comp.modernBadge": "Comfy Colab Kit",
    "comp.modernTitle": "Remote & Autonomous Gate",
    "comp.m1Title": "Remote Wake Anywhere",
    "comp.m1Desc": "Wake from your phone, tablet, or laptop with one tap. No open Colab tabs or PC required.",
    "comp.m2Title": "Single Persistent Custom Domain",
    "comp.m2Desc": "Stable custom URL fronted by Caddy reverse proxy; background gateway persists independently.",
    "comp.m3Title": "Cloudflare Access Zero-Trust",
    "comp.m3Desc": "Protected by enterprise SSO and Email OTP. Granular owner roles and service tokens.",
    "comp.m4Title": "Autonomous AI Agent Native",
    "comp.m4Desc": "Claude, OpenClaw, or LangGraph agents can wake, generate, and spin down via REST/MCP.",
    "comp.m5Title": "Smart Auto-Shutdown",
    "comp.m5Desc": "Monitors idle activity, max session duration, and CU hard caps to automatically release the GPU.",
    "features.badge": "Capabilities",
    "features.titlePrefix": "Feature",
    "features.titleShiny": "Highlights",
    "features.lead": "Engineered for friction-free generation with enterprise-grade session governance.",
    "features.f1Tag": "Instant Launch",
    "features.f1Title": "One-Click Remote Wake",
    "features.f1Desc": "Wake from your phone, laptop, or API. The gate provisions Colab, configures ComfyUI, and proxies it on the same URL.",
    "features.f2Tag": "Zero-Trust Auth",
    "features.f2Title": "Cloudflare Access Security",
    "features.f2Desc": "Protected by zero-trust identity verification (SSO / Email OTP / Service Tokens). No exposed ports, no shared Google passwords.",
    "features.f3Tag": "Idempotent",
    "features.f3Title": "Single Shared Session",
    "features.f3Desc": "Wake is strictly idempotent. Everyone sees identical live progress, and a second visitor joins the active VM without duplicating cost.",
    "features.f4Tag": "Budget Guard",
    "features.f4Title": "Smart Auto-Shutdown",
    "features.f4Desc": "Sessions automatically go back to sleep on idle timeout, max duration limit, or when the allocated CU budget is reached.",
    "features.f5Tag": "App First",
    "features.f5Title": "App Mode by Default",
    "features.f5Desc": "Boots straight into ComfyUI App mode: type a prompt, get an image. Switch back to the full node graph anytime with one click.",
    "features.f6Tag": "AI Agent Ready",
    "features.f6Title": "AI Agent & MCP Native",
    "features.f6Desc": "Headless AI coding agents can wake the gate, trigger image generation, and release GPUs via REST API and MCP protocol.",
    "features.roadmapTag": "Roadmap",
    "features.roadmapDesc": "MCP server integration based on joenorton/comfyui-mcp-server, mounted at /mcp so AI agents can generate images through your gate.",
    "profiles.badge": "Configurations",
    "profiles.titleShiny": "Model Profiles",
    "profiles.lead": "A profile is a single profile.yaml: public model weights, community workflows, and ComfyUI args. Pick one before waking.",
    "profiles.p1Tag": "Recommended",
    "profiles.p1Desc": "Primary profile: OpenMOSS MOSS-Image on a Colab L4, defaulting to MATLOWAI DeBand int8 workflow with 4-step turbo and official quality graphs.",
    "profiles.p2Tag": "Fast Turbo",
    "profiles.p2Desc": "Stable Diffusion 3.5 on a Colab L4, defaulting to the official SD 3.5 Large fp8 graph with TurboX and Medium turbo fast variants.",
    "profiles.p3Tag": "Int8 Quant",
    "profiles.p3Desc": "Qwen-Image 2.1 official templates on a Colab L4, defaulting to 2.1 Turbo text-to-image with int8 weights that effortlessly fit 24 GB.",
    "numbers.badge": "Real-World",
    "numbers.titlePrefix": "Measured",
    "numbers.titleShiny": "Benchmarks",
    "numbers.lead": "Transparent metrics measured on Google Colab L4 GPU infrastructure.",
    "numbers.s1Label": "Colab L4 compute cost per hour",
    "numbers.s2Label": "One 1024² image, 25 steps",
    "numbers.s3Label": "Cold start from sleep to ready",
    "numbers.footnote": "Measured on Colab L4 with the MOSS-Image profile. Actual figures will vary depending on network and GPU quota.",
    "quickstart.badge": "Deployment",
    "quickstart.titlePrefix": "Quick",
    "quickstart.titleShiny": "Start",
    "quickstart.lead": "Deploy the gate on a lightweight VPS behind Cloudflare Access, then wake Colab from your browser or AI agent. Full production walkthrough in docs/AGENT.md.",
    "quickstart.btn": "Read the Deploy Guide",
    "quickstart.github": "Star on GitHub",
    "faq.badge": "Help & Answers",
    "faq.titleShiny": "FAQ",
    "faq.lead": "Common questions about compute, quotas, workflows, and self-hosting.",
    "faq.q1": "Is it free?",
    "faq.a1": "The kit is open source under the MIT license. Colab compute comes from your own Google account: free tier or paid compute units.",
    "faq.q2": "Can I share it with friends?",
    "faq.a2": "It is designed for personal or small trusted team use. Colab compute belongs to your personal Google account. Keep access to yourself or invite friends via Cloudflare Access emails rather than making it public.",
    "faq.q3": "What happens when nobody uses it?",
    "faq.a3": "It shuts down automatically on idle timeout, max session duration, or when it reaches your CU budget limit, and the gate safely goes back to sleep.",
    "faq.q4": "Do I lose the node editor?",
    "faq.a4": "Not at all. It opens in clean App mode by default for speedy generation, and a single toggle in the top bar switches to the full ComfyUI node graph editor.",
    "faq.q5": "How do I add a workflow?",
    "faq.a5": "Add it to a profile.yaml and run git pull on the gate VPS. No container rebuilds or restarts required.",
    "faq.q6": "Is it affiliated with Google or Comfy Org?",
    "faq.a6": "No. It is an independent open-source project created by the community.",
    "footer.desc": "Remote, wake-on-demand ComfyUI on Google Colab GPUs. One URL, zero idle waste.",
    "footer.badge": "Open Source & Self-Hostable",
    "footer.navTitle": "Navigation",
    "footer.docsTitle": "Documentation",
    "footer.ecoTitle": "Ecosystem",
    "footer.disclaimer": "Not affiliated with Google LLC or Comfy Org."
  },
  zh: {
    metaTitle: "Comfy Colab Kit — 远程按需唤醒 Google Colab 上的 ComfyUI",
    metaDesc: "随时随地在手机、浏览器或通过 AI Agent 远程按需唤醒 Google Colab GPU，Cloudflare Access 零信任鉴权，闲置自动休眠关机。",
    "nav.problem": "痛点分析",
    "nav.how": "工作原理",
    "nav.comp": "方案对比",
    "nav.features": "核心特性",
    "nav.profiles": "预设配置",
    "nav.numbers": "实测数据",
    "nav.quickstart": "快速上手",
    "nav.faq": "常见问题",
    "hero.badge": "按需唤醒 ComfyUI",
    "hero.stateSleep": "休眠中",
    "hero.stateWake": "唤醒中",
    "hero.stateReady": "已就绪",
    "hero.title": "部署在 Colab 上的 ComfyUI，只在需要时唤醒",
    "hero.sub": "彻底告别在电脑前死守网页 Colab 标签页的繁琐体验。Comfy Colab Kit 让你可以随时在手机、平板或通过 AI Agent 远程一键唤醒 Google Colab L4 GPU，由专属轻量 VPS 与 Cloudflare Access 零信任网关守护，空闲全自动关机释放算力。",
    "hero.btnPrimary": "快速上手",
    "hero.btnGitHub": "GitHub 源码",
    "hero.stat": "L4 GPU · ~1.54 CU/h · 28.4 秒生成 1024²",
    "problem.badge": "痛点分析",
    "problem.titlePrefix": "面对的",
    "problem.titleShiny": "核心痛点",
    "problem.lead": "全天候租用独立 GPU 成本高昂，而原生 Colab 强制你坐在电脑前人工守着标签页，容易断连超时。",
    "problem.c1Tag": "空置浪费",
    "problem.c1Title": "云端 GPU 全天候计费昂贵",
    "problem.c1Desc": "哪怕数小时没有生成任何图片，全天候租赁的云端 GPU 依然持续计费，资金白白浪费。",
    "problem.c2Tag": "绑在电脑前",
    "problem.c2Title": "原生 Colab 网页脆弱繁琐",
    "problem.c2Desc": "必须守在电脑前手动点单元格；笔记本休眠或网络微抖立即断连丢失进度，公网 URL 每次变化。",
    "problem.c3Tag": "协作与 Agent 阻碍",
    "problem.c3Title": "无法远程共享与自动化",
    "problem.c3Desc": "多人协作容易多开虚拟机消耗算力；无头 AI Agent 无法通过标准化接口自动化拉起与出图。",
    "problem.closing": "Comfy Colab Kit 为 Colab 提供唯一定点入口：随时随地远程唤醒，闲置自动休眠释放。",
    "how.badge": "架构原理",
    "how.titlePrefix": "底层",
    "how.titleShiny": "工作原理",
    "how.lead": "轻量、零信任的安全桥梁，连接多端设备、AI Agent 与 Colab 按需算力。",
    "flow.browserTitle": "浏览器 / AI Agent",
    "flow.browserDesc": "手机、网页或 API",
    "flow.cfDesc": "零信任身份校验",
    "flow.gateTitle": "VPS 统一网关",
    "flow.gateDesc": "Caddy + comfy-gate",
    "flow.colabDesc": "ComfyUI 运行内核",
    "how.s1": "访问专属域名或调用 API，Cloudflare Access 零信任网关在流量接触基础设施前完成身份校验。",
    "how.s2": "若 GPU 处于休眠状态，网关呈现唤醒卡片（或承接 Agent 调度）；轻点「唤醒」即可启动。",
    "how.s3": "网关全自动分配 Colab L4、初始化 ComfyUI 并按需拉取模型，多端实时同步阶段进度与控制台日志。",
    "how.s4": "就绪后该域名直接无缝反代官方原版 ComfyUI Web 应用；闲置超时或预算达到上限时自动安全停机休眠。",
    "how.demoLabel": "正在唤醒… 正在同步模型",
    "comp.badge": "工作流革命",
    "comp.titlePrefix": "为什么选择",
    "comp.titleShiny": "Comfy Colab Kit",
    "comp.lead": "直观对比传统 Colab 网页操作与全自动按需网关的体验差异。",
    "comp.legacyBadge": "传统方式",
    "comp.legacyTitle": "原生 Colab 网页笔记本",
    "comp.l1Title": "被电脑和浏览器绑架",
    "comp.l1Desc": "必须坐在电脑前，在浏览器标签页逐个点击单元格执行代码并守着屏幕。",
    "comp.l2Title": "会话极其脆弱易断连",
    "comp.l2Desc": "电脑睡眠或网络波动导致运行断连；每次重启分配的 URL 均不一致。",
    "comp.l3Title": "毫无安全鉴权机制",
    "comp.l3Desc": "要么使用暴露公网的匿名隧道，要么必须共享个人的 Google 账号密码。",
    "comp.l4Title": "AI Agent 无法调用",
    "comp.l4Desc": "缺乏标准 Headless 接口与 MCP 端点，自动化智能体无法直接驱动。",
    "comp.l5Title": "整晚遗忘导致点数烧光",
    "comp.l5Desc": "用完忘记关闭标签页，Colab 将持续扣除昂贵的算力单元 (CU)。",
    "comp.modernBadge": "全新方案",
    "comp.modernTitle": "远程按需智能网关",
    "comp.m1Title": "随时随地跨端远程唤醒",
    "comp.m1Desc": "在手机、平板或电脑一键轻点唤醒，无需打开 Colab 网页，后台自动就绪。",
    "comp.m2Title": "专属固定域名与稳定反代",
    "comp.m2Desc": "绑定个人专属域名，Caddy 反代稳定如常，网关后台常驻，断网无缝重连。",
    "comp.m3Title": "Cloudflare Access 零信任保护",
    "comp.m3Desc": "企业级 SSO / 邮箱验证码白名单拦截未授权访问，专属服务令牌分配给 Agent。",
    "comp.m4Title": "原生支持 AI Agent 自动化",
    "comp.m4Desc": "为 Claude、OpenClaw、LangGraph 提供标准化 REST 接口与 MCP 端点直接生图。",
    "comp.m5Title": "智能闲置检测与预算兜底",
    "comp.m5Desc": "支持 20 分钟闲置、单次最长会话与 CU 硬上限自动关机，绝不浪费算力。",
    "features.badge": "功能特性",
    "features.titlePrefix": "系统",
    "features.titleShiny": "核心特性",
    "features.lead": "专为高效、零心智负担的图像生成打造，兼顾跨端远程控制与成本预算兜底。",
    "features.f1Tag": "瞬时就绪",
    "features.f1Title": "一键跨端远程唤醒",
    "features.f1Desc": "在手机、电脑或通过 API 一键唤醒，自动拉起 Colab 实例并配置 ComfyUI，同一 URL 自动反代。",
    "features.f2Tag": "零信任鉴权",
    "features.f2Title": "Cloudflare Access 严密防护",
    "features.f2Desc": "集成 Cloudflare 零信任身份校验（SSO / 邮箱验证码 / 服务令牌），无需开放公网端口，不泄露 Google 密码。",
    "features.f3Tag": "全局幂等",
    "features.f3Title": "全局单例共享会话",
    "features.f3Desc": "唤醒操作全局唯一且幂等。多位访问者实时共享启动进度与控制台日志，杜绝重复开机扣费。",
    "features.f4Tag": "成本兜底",
    "features.f4Title": "闲置智能停机休眠",
    "features.f4Desc": "支持无请求闲置超时、单次最长会话以及 Colab 算力单元 (CU) 预算硬上限自动关机。",
    "features.f5Tag": "极简交互",
    "features.f5Title": "首发纯享 App 模式",
    "features.f5Desc": "开箱直达 ComfyUI 轻量 App 模式，输入提示词即刻生图；随时可通过顶部开关无缝切回全功能节点流。",
    "features.f6Tag": "Agent 友好",
    "features.f6Title": "AI Agent 原生集成",
    "features.f6Desc": "无头 AI 编程智能体可通过标准 REST 接口或 MCP 协议直接唤醒网关、驱动出图并自动释放算力。",
    "features.roadmapTag": "路线规划",
    "features.roadmapDesc": "即将支持基于 joenorton/comfyui-mcp-server 的 MCP 协议端点，赋能各类 AI Agent 通过该网关直接调用图像生成工作流。",
    "profiles.badge": "预设矩阵",
    "profiles.titleShiny": "精选模型预设",
    "profiles.lead": "Profile 由单个 profile.yaml 驱动：声明模型权重下载源、社区精选工作流与 ComfyUI 启动参数，唤醒时随心切换。",
    "profiles.p1Tag": "主力推荐",
    "profiles.p1Desc": "官方主力预设：基于 OpenMOSS MOSS-Image 架构适配 Colab L4，默认搭载 MATLOWAI DeBand int8 高清去色带与 4 步 Turbo 工作流。",
    "profiles.p2Tag": "极速生图",
    "profiles.p2Desc": "部署 SD 3.5 Large fp8 官方节点流，并集成 TurboX 与 Medium 极速生图变体。",
    "profiles.p3Tag": "低显存优化",
    "profiles.p3Desc": "适配千问 Qwen-Image 2.1 官方生图方案，经 int8 量化轻量优化，完美适配 24 GB 显存。",
    "numbers.badge": "实测基准",
    "numbers.titlePrefix": "硬件",
    "numbers.titleShiny": "实测基准数据",
    "numbers.lead": "基于 Google Colab 真实云端环境严谨测量与验证。",
    "numbers.s1Label": "Colab L4 每小时算力点 (CU) 消耗",
    "numbers.s2Label": "单张 1024² 图像生成耗时 (25 步)",
    "numbers.s3Label": "冷启动拉起至完全就绪耗时",
    "numbers.footnote": "基于 Google Colab L4 运行 MOSS-Image 预设实测。实际耗时可能因当时网络速度与算力负载略有浮动。",
    "quickstart.badge": "部署指南",
    "quickstart.titlePrefix": "快速",
    "quickstart.titleShiny": "部署上手",
    "quickstart.lead": "在一台轻量 VPS 上部署反代网关并接入 Cloudflare Access，即可在手机或电脑随时唤醒 Colab。完整生产级指南详见 docs/AGENT.md。",
    "quickstart.btn": "查看完整部署指南",
    "quickstart.github": "前往 GitHub 点赞",
    "faq.badge": "疑问解答",
    "faq.titleShiny": "常见问题解答",
    "faq.lead": "关于算力、费用、权限、工作流与私有部署的高频问题汇总。",
    "faq.q1": "使用该项目是完全免费的吗？",
    "faq.a1": "本套件代码完全基于 MIT 许可开源。Colab 算力消耗基于您个人的 Google 账号（可使用免费分配额度或自购 CU 算力点）。",
    "faq.q2": "我可以将唤醒网址分享给朋友或团队吗？",
    "faq.a2": "建议个人或小范围协作使用。Colab 算力归属个人 Google 账号，可通过 Cloudflare Access 白名单指定受信任的邮箱登录，请勿对外公开开放。",
    "faq.q3": "当无人使用时，系统如何处理？",
    "faq.a3": "网关会在检测到闲置超时、单次最长会话超时或到达算力点预算阈值时自动停止 VM，安全切回休眠状态，不再产生计费。",
    "faq.q4": "默认进入 App 模式后还能使用完整的节点编辑器吗？",
    "faq.a4": "完全不受影响。系统默认以极简 App 模式呈现以便快速出图，只需点击顶部工具栏的模式切换按钮，即可无缝进入完整的节点图画布。",
    "faq.q5": "如何添加自定义工作流与模型？",
    "faq.a5": "只需在 profile.yaml 中声明 HuggingFace 模型直链并放入工作流 JSON，在网关 VPS 执行一次 git pull 即可即时生效，无需重新构建容器。",
    "faq.q6": "本项目与 Google 或 Comfy Org 有官方关联吗？",
    "faq.a6": "没有关联。Comfy Colab Kit 是一个独立的社区开源项目，旨在为创作者提供优雅经济的按需算力方案。",
    "footer.desc": "远程按需唤醒 Google Colab GPU，一键启动 ComfyUI。单一入口，告别闲置浪费。",
    "footer.badge": "开源且支持私有化部署",
    "footer.navTitle": "产品导航",
    "footer.docsTitle": "技术文档",
    "footer.ecoTitle": "开源生态",
    "footer.disclaimer": "本项目与 Google LLC 或 Comfy Org 无官方关联。"
  }
};

function applyLanguage(lang) {
  const dict = I18N[lang] || I18N.en;
  root.lang = lang;
  localStorage.setItem("kit.lang", lang);

  // Update document title and meta
  if (dict.metaTitle) document.title = dict.metaTitle;
  const metaDesc = document.querySelector('meta[name="description"]');
  if (metaDesc && dict.metaDesc) metaDesc.content = dict.metaDesc;

  // Update elements with data-i18n
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const key = el.getAttribute("data-i18n");
    if (dict[key] != null) {
      el.textContent = dict[key];
    }
  });

  // Update language trigger label
  const langLabel = document.getElementById("lang-label");
  if (langLabel) {
    langLabel.textContent = lang === "zh" ? "简体中文" : "English";
  }

  // Update active status on popover items
  document.querySelectorAll("#lang-popover .menu-item").forEach((item) => {
    item.classList.toggle("active", item.getAttribute("data-lang") === lang);
  });
}

// Initial language detection
const savedLang = localStorage.getItem("kit.lang");
const initialLang = savedLang || (navigator.language && navigator.language.toLowerCase().startsWith("zh") ? "zh" : "en");
applyLanguage(initialLang);

// Language dropdown setup
const langBtn = document.getElementById("lang-btn");
const langPopover = document.getElementById("lang-popover");
const langMenu = document.getElementById("lang-menu");

if (langBtn && langPopover) {
  langBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    const isOpen = langPopover.classList.toggle("open");
    langMenu?.classList.toggle("lang-open", isOpen);
    langBtn.setAttribute("aria-expanded", isOpen ? "true" : "false");
  });

  document.querySelectorAll("#lang-popover .menu-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      const nextLang = btn.getAttribute("data-lang");
      if (nextLang) applyLanguage(nextLang);
      langPopover.classList.remove("open");
      langMenu?.classList.remove("lang-open");
      langBtn.setAttribute("aria-expanded", "false");
    });
  });

  document.addEventListener("click", (e) => {
    if (!langMenu?.contains(e.target)) {
      langPopover.classList.remove("open");
      langMenu?.classList.remove("lang-open");
      langBtn.setAttribute("aria-expanded", "false");
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      langPopover.classList.remove("open");
      langMenu?.classList.remove("lang-open");
      langBtn.setAttribute("aria-expanded", "false");
    }
  });
}
