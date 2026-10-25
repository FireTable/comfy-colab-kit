import fs from 'node:fs';
import path from 'node:path';
import { execSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const rootDir = path.resolve(__dirname, '..');

const chromePath = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
if (!fs.existsSync(chromePath)) {
  console.error('Google Chrome not found at:', chromePath);
  process.exit(1);
}

// Convert files to base64
function toBase64(relPath, mimeType) {
  const fullPath = path.resolve(rootDir, relPath);
  const data = fs.readFileSync(fullPath).toString('base64');
  return `data:${mimeType};base64,${data}`;
}

const logoSvg = fs.readFileSync(path.resolve(rootDir, 'apps/site/assets/logo-e-fusion-dark.svg'), 'utf8');
const logoBase64 = `data:image/svg+xml;utf8,${encodeURIComponent(logoSvg)}`;

const wakePng = toBase64('drafts/images/preview-wake.png', 'image/png');
const comfyPng = toBase64('drafts/images/preview-comfyui.png', 'image/png');
const agentPng = toBase64('drafts/images/agent-txt2img.png', 'image/png');

// 1. Template for Site OG Image (Showcase & Docs)
const siteHtml = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body {
    width: 1200px;
    height: 630px;
    overflow: hidden;
    background: #09060D;
    color: #F4F1F6;
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Inter", "Segoe UI", Roboto, sans-serif;
    -webkit-font-smoothing: antialiased;
  }

  .canvas {
    position: relative;
    width: 1200px;
    height: 630px;
    padding: 44px 52px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    overflow: hidden;
  }

  /* Background Glows */
  .glow-1 {
    position: absolute;
    top: -120px;
    left: 280px;
    width: 550px;
    height: 550px;
    background: radial-gradient(circle, rgba(232, 113, 10, 0.22) 0%, rgba(249, 171, 0, 0.08) 40%, transparent 70%);
    pointer-events: none;
    z-index: 0;
  }
  .glow-2 {
    position: absolute;
    bottom: -100px;
    right: 20px;
    width: 600px;
    height: 600px;
    background: radial-gradient(circle, rgba(123, 75, 168, 0.28) 0%, rgba(232, 113, 10, 0.08) 45%, transparent 70%);
    pointer-events: none;
    z-index: 0;
  }

  /* Cyber Grid */
  .grid {
    position: absolute;
    inset: 0;
    background-image: 
      linear-gradient(rgba(255, 255, 255, 0.04) 1px, transparent 1px),
      linear-gradient(90deg, rgba(255, 255, 255, 0.04) 1px, transparent 1px);
    background-size: 40px 40px;
    mask-image: radial-gradient(ellipse at 50% 50%, #000 30%, transparent 80%);
    -webkit-mask-image: radial-gradient(ellipse at 50% 50%, #000 30%, transparent 80%);
    pointer-events: none;
    z-index: 1;
  }

  /* Top Bar */
  .top-bar {
    position: relative;
    z-index: 2;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 14px;
  }
  .logo-img {
    width: 44px;
    height: 44px;
    border-radius: 10px;
    box-shadow: 0 4px 16px rgba(232, 113, 10, 0.35);
  }
  .brand-name {
    font-size: 24px;
    font-weight: 700;
    letter-spacing: -0.02em;
    color: #FFFFFF;
  }
  .badge {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 6px 14px;
    background: rgba(255, 255, 255, 0.06);
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 999px;
    font-size: 13px;
    font-weight: 600;
    color: #F9AB00;
    letter-spacing: 0.03em;
    text-transform: uppercase;
    backdrop-filter: blur(12px);
  }
  .badge-dot {
    width: 7px;
    height: 7px;
    background: #3DDC84;
    border-radius: 50%;
    box-shadow: 0 0 8px #3DDC84;
  }

  /* Main Body Split */
  .main {
    position: relative;
    z-index: 2;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 40px;
    margin-top: 4px;
  }

  .content-col {
    flex: 1;
    max-width: 580px;
  }

  .eyebrow {
    display: inline-block;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #E8710A;
    margin-bottom: 12px;
  }

  .title {
    font-size: 44px;
    font-weight: 800;
    line-height: 1.15;
    letter-spacing: -0.03em;
    color: #FFFFFF;
    margin-bottom: 16px;
    background: linear-gradient(135deg, #FFFFFF 30%, #F9AB00 70%, #E8710A 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }

  .desc {
    font-size: 18px;
    line-height: 1.5;
    color: #A49CAB;
    margin-bottom: 24px;
    font-weight: 400;
  }
  .desc strong {
    color: #F4F1F6;
    font-weight: 600;
  }

  .tags-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 10px;
  }
  .tag {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 9px 14px;
    background: rgba(33, 25, 39, 0.65);
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 10px;
    font-size: 13.5px;
    font-weight: 500;
    color: #E6E1EB;
    backdrop-filter: blur(8px);
  }
  .tag-icon {
    font-size: 16px;
  }

  /* Mockup Visual */
  .visual-col {
    position: relative;
    width: 480px;
    height: 380px;
  }

  .window-mockup {
    position: absolute;
    width: 460px;
    height: 300px;
    right: 0;
    top: 20px;
    background: #1A141F;
    border: 1px solid rgba(255, 255, 255, 0.15);
    border-radius: 14px;
    overflow: hidden;
    box-shadow: 
      0 24px 60px -10px rgba(0, 0, 0, 0.8),
      0 0 40px rgba(249, 171, 0, 0.12);
  }

  .window-header {
    height: 32px;
    background: rgba(255, 255, 255, 0.04);
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    display: flex;
    align-items: center;
    padding: 0 12px;
    gap: 7px;
  }
  .dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
  }
  .dot-red { background: #FF5F56; }
  .dot-yellow { background: #FFBD2E; }
  .dot-green { background: #27C93F; }
  .window-title {
    font-size: 11px;
    color: #7B7283;
    font-family: monospace;
    margin-left: 8px;
    letter-spacing: -0.01em;
  }

  .window-content {
    width: 100%;
    height: calc(100% - 32px);
    overflow: hidden;
    position: relative;
  }
  .window-content img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    object-position: top center;
  }

  /* Secondary floating overlay card */
  .floating-card {
    position: absolute;
    left: -24px;
    bottom: -15px;
    width: 250px;
    height: 155px;
    background: rgba(20, 14, 25, 0.92);
    border: 1px solid rgba(249, 171, 0, 0.35);
    border-radius: 12px;
    box-shadow: 
      0 16px 36px rgba(0, 0, 0, 0.75),
      0 0 24px rgba(232, 113, 10, 0.2);
    backdrop-filter: blur(16px);
    overflow: hidden;
  }
  .floating-card img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    object-position: center;
  }

  /* Footer */
  .footer {
    position: relative;
    z-index: 2;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding-top: 14px;
    border-top: 1px solid rgba(255, 255, 255, 0.08);
    font-size: 14px;
    color: #8C8494;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  .footer-domain {
    color: #F9AB00;
    font-weight: 600;
  }
</style>
</head>
<body>
<div class="canvas">
  <div class="glow-1"></div>
  <div class="glow-2"></div>
  <div class="grid"></div>

  <div class="top-bar">
    <div class="brand">
      <img class="logo-img" src="${logoBase64}" alt="Logo">
      <span class="brand-name">Comfy Colab Kit</span>
    </div>
    <div class="badge">
      <span class="badge-dot"></span>
      Open Source · MIT License
    </div>
  </div>

  <div class="main">
    <div class="content-col">
      <span class="eyebrow">Google Colab · Remote GPU Runtime</span>
      <h1 class="title">ComfyUI on Colab L4 GPUs, Awake Only When Needed</h1>
      <p class="desc">
        Remote, wake-on-demand GPU compute. <strong>Zero idle cost</strong> with automated 30-minute shutdown. Protected by <strong>Cloudflare Zero-Trust</strong> auth with native <strong>AI Agent & MCP</strong> dispatch.
      </p>
      <div class="tags-grid">
        <div class="tag"><span class="tag-icon">⚡</span> Wake on Demand</div>
        <div class="tag"><span class="tag-icon">🛡️</span> Cloudflare Access</div>
        <div class="tag"><span class="tag-icon">🤖</span> MCP & AI Agent Skill</div>
        <div class="tag"><span class="tag-icon">⏱️</span> 30m Auto-Shutdown</div>
      </div>
    </div>

    <div class="visual-col">
      <div class="window-mockup">
        <div class="window-header">
          <div class="dot dot-red"></div>
          <div class="dot dot-yellow"></div>
          <div class="dot dot-green"></div>
          <span class="window-title">comfy-colab-kit.firetable.tech · ComfyUI</span>
        </div>
        <div class="window-content">
          <img src="${comfyPng}" alt="ComfyUI Canvas Preview">
        </div>
      </div>

      <div class="floating-card">
        <img src="${wakePng}" alt="Wake Card UI">
      </div>
    </div>
  </div>

  <div class="footer">
    <span class="footer-domain">comfy-colab-kit.firetable.tech</span>
    <span>github.com/FireTable/comfy-colab-kit</span>
  </div>
</div>
</body>
</html>`;

// 2. Template for Gate OG Image (Wake & Gateway App)
const gateHtml = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body {
    width: 1200px;
    height: 630px;
    overflow: hidden;
    background: #09060D;
    color: #F4F1F6;
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Inter", "Segoe UI", Roboto, sans-serif;
    -webkit-font-smoothing: antialiased;
  }

  .canvas {
    position: relative;
    width: 1200px;
    height: 630px;
    padding: 44px 52px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    overflow: hidden;
  }

  /* Intense golden/orange circuit glow */
  .glow-1 {
    position: absolute;
    top: -100px;
    right: 200px;
    width: 600px;
    height: 600px;
    background: radial-gradient(circle, rgba(249, 171, 0, 0.22) 0%, rgba(232, 113, 10, 0.12) 40%, transparent 70%);
    pointer-events: none;
    z-index: 0;
  }
  .glow-2 {
    position: absolute;
    bottom: -150px;
    left: -50px;
    width: 550px;
    height: 550px;
    background: radial-gradient(circle, rgba(232, 113, 10, 0.20) 0%, transparent 65%);
    pointer-events: none;
    z-index: 0;
  }

  .grid {
    position: absolute;
    inset: 0;
    background-image: 
      linear-gradient(rgba(249, 171, 0, 0.05) 1px, transparent 1px),
      linear-gradient(90deg, rgba(249, 171, 0, 0.05) 1px, transparent 1px);
    background-size: 38px 38px;
    mask-image: radial-gradient(ellipse at 50% 50%, #000 30%, transparent 80%);
    -webkit-mask-image: radial-gradient(ellipse at 50% 50%, #000 30%, transparent 80%);
    pointer-events: none;
    z-index: 1;
  }

  .top-bar {
    position: relative;
    z-index: 2;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 14px;
  }
  .logo-img {
    width: 44px;
    height: 44px;
    border-radius: 10px;
    box-shadow: 0 4px 16px rgba(249, 171, 0, 0.4);
  }
  .brand-name {
    font-size: 24px;
    font-weight: 700;
    letter-spacing: -0.02em;
    color: #FFFFFF;
  }
  .badge {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 6px 14px;
    background: rgba(249, 171, 0, 0.08);
    border: 1px solid rgba(249, 171, 0, 0.25);
    border-radius: 999px;
    font-size: 13px;
    font-weight: 600;
    color: #F9AB00;
    letter-spacing: 0.03em;
    text-transform: uppercase;
  }
  .badge-pulse {
    width: 8px;
    height: 8px;
    background: #3DDC84;
    border-radius: 50%;
    box-shadow: 0 0 10px #3DDC84;
  }

  .main {
    position: relative;
    z-index: 2;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 40px;
    margin-top: 4px;
  }

  .content-col {
    flex: 1;
    max-width: 580px;
  }

  .eyebrow {
    display: inline-block;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #F9AB00;
    margin-bottom: 12px;
  }

  .title {
    font-size: 44px;
    font-weight: 800;
    line-height: 1.15;
    letter-spacing: -0.03em;
    color: #FFFFFF;
    margin-bottom: 16px;
    background: linear-gradient(135deg, #FFFFFF 20%, #F9AB00 65%, #E8710A 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }

  .desc {
    font-size: 18px;
    line-height: 1.5;
    color: #A49CAB;
    margin-bottom: 24px;
  }
  .desc strong {
    color: #F4F1F6;
    font-weight: 600;
  }

  .tags-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 10px;
  }
  .tag {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 9px 14px;
    background: rgba(33, 25, 39, 0.7);
    border: 1px solid rgba(249, 171, 0, 0.18);
    border-radius: 10px;
    font-size: 13.5px;
    font-weight: 500;
    color: #E6E1EB;
    backdrop-filter: blur(8px);
  }
  .tag-icon {
    font-size: 16px;
  }

  .visual-col {
    position: relative;
    width: 480px;
    height: 380px;
  }

  .window-mockup {
    position: absolute;
    width: 460px;
    height: 320px;
    right: 0;
    top: 15px;
    background: #15101A;
    border: 1px solid rgba(249, 171, 0, 0.28);
    border-radius: 14px;
    overflow: hidden;
    box-shadow: 
      0 24px 60px -10px rgba(0, 0, 0, 0.85),
      0 0 35px rgba(249, 171, 0, 0.16);
  }

  .window-header {
    height: 32px;
    background: rgba(255, 255, 255, 0.04);
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    display: flex;
    align-items: center;
    padding: 0 12px;
    gap: 7px;
  }
  .dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
  }
  .dot-red { background: #FF5F56; }
  .dot-yellow { background: #FFBD2E; }
  .dot-green { background: #27C93F; }
  .window-title {
    font-size: 11px;
    color: #A49CAB;
    font-family: monospace;
    margin-left: 8px;
  }

  .window-content {
    width: 100%;
    height: calc(100% - 32px);
    overflow: hidden;
    position: relative;
  }
  .window-content img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    object-position: center;
  }

  .footer {
    position: relative;
    z-index: 2;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding-top: 14px;
    border-top: 1px solid rgba(255, 255, 255, 0.08);
    font-size: 14px;
    color: #8C8494;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  .footer-domain {
    color: #F9AB00;
    font-weight: 600;
  }
</style>
</head>
<body>
<div class="canvas">
  <div class="glow-1"></div>
  <div class="glow-2"></div>
  <div class="grid"></div>

  <div class="top-bar">
    <div class="brand">
      <img class="logo-img" src="${logoBase64}" alt="Logo">
      <span class="brand-name">Comfy Colab Gate</span>
    </div>
    <div class="badge">
      <span class="badge-pulse"></span>
      Gateway Control Portal
    </div>
  </div>

  <div class="main">
    <div class="content-col">
      <span class="eyebrow">Autonomous Session Manager</span>
      <h1 class="title">Remote GPU Gateway & Session Operator</h1>
      <p class="desc">
        One URL, one shared session. Direct pass-through to official <strong>ComfyUI on Colab L4 GPUs</strong> with real-time <strong>CU budget monitoring</strong>, pre-baked model profiles, and automated idle cutoff.
      </p>
      <div class="tags-grid">
        <div class="tag"><span class="tag-icon">🟢</span> One-Click GPU Wake</div>
        <div class="tag"><span class="tag-icon">💳</span> Real-Time CU Metering</div>
        <div class="tag"><span class="tag-icon">🔒</span> Cloudflare Zero-Trust</div>
        <div class="tag"><span class="tag-icon">🔌</span> ComfyUI App & Graph</div>
      </div>
    </div>

    <div class="visual-col">
      <div class="window-mockup">
        <div class="window-header">
          <div class="dot dot-red"></div>
          <div class="dot dot-yellow"></div>
          <div class="dot dot-green"></div>
          <span class="window-title">gateway · wake & compute control</span>
        </div>
        <div class="window-content">
          <img src="${wakePng}" alt="Gate Wake Control UI">
        </div>
      </div>
    </div>
  </div>

  <div class="footer">
    <span class="footer-domain">comfy.example.com</span>
    <span>Zero-Trust Compute Bridge · Powered by Comfy Colab Kit</span>
  </div>
</div>
</body>
</html>`;

fs.writeFileSync('/tmp/site-og.html', siteHtml);
fs.writeFileSync('/tmp/gate-og.html', gateHtml);

console.log('Rendering Site OG image (1200x630)...');
execSync(`"${chromePath}" --headless=new --disable-gpu --hide-scrollbars --window-size=1200,630 --screenshot=/tmp/og-site.png file:///tmp/site-og.html`);

console.log('Rendering Gate OG image (1200x630)...');
execSync(`"${chromePath}" --headless=new --disable-gpu --hide-scrollbars --window-size=1200,630 --screenshot=/tmp/og-gate.png file:///tmp/gate-og.html`);

// Save to destination paths
const siteDest = path.resolve(rootDir, 'apps/site/assets/og.png');
const siteDestNamed = path.resolve(rootDir, 'apps/site/assets/og-site.png');
const gateDest = path.resolve(rootDir, 'apps/gate/web/og.png');
const gateDestNamed = path.resolve(rootDir, 'apps/gate/web/og-gate.png');

fs.copyFileSync('/tmp/og-site.png', siteDest);
fs.copyFileSync('/tmp/og-site.png', siteDestNamed);
fs.copyFileSync('/tmp/og-gate.png', path.resolve(rootDir, 'apps/site/assets/og-gate.png'));
fs.copyFileSync('/tmp/og-gate.png', gateDest);
fs.copyFileSync('/tmp/og-gate.png', gateDestNamed);

console.log('✅ Generated:');
console.log('  -', siteDest);
console.log('  -', siteDestNamed);
console.log('  -', gateDest);
console.log('  -', gateDestNamed);
