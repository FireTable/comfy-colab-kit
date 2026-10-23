# Wake Page & Overlay Design Specification

> [!NOTE]
> **Architecture Clarification**: The wake page exists strictly during cold-start, stopping, and sleeping states. Once the session reaches `ready`, the user interacts with the **genuine, full-featured ComfyUI Web Application** directly, with full access to ComfyUI's native frontend, extensions, custom node canvases, and the App mode interface.

---

## 1. Visual UI Layout & Wireframes

### Phase 1: The Sleeping & Booting Wake Card (`/`)

The Wake page is served on `/` whenever the session state is not `ready`. Inspired by the sleeping state of Hugging Face Spaces, it presents a single, beautifully centered card over a WebGL Aurora background:

```
┌──────────────────────────────────────────────────────────────┐
│  Comfy Colab Kit               [ 🌐 简体中文 ∨ ] [ ☼ ] [  ] │
│                                                              │
│            ┌────────────────────────────────────┐            │
│            │   ● ComfyUI is sleeping     1 online │          │
│            │                                    │            │
│            │       ⚡ Comfy Colab Kit            │            │
│            │   On-demand GPU generation on Colab │            │
│            │                                    │            │
│            │   [ Profile: MOSS-Image (L4) ▼ ]   │            │
│            │                                    │            │
│            │         [ ⚡ Wake Session ]         │            │
│            │                                    │            │
│            │  CU Balance: 88.0 · Spent Today: 0.4│           │
│            └────────────────────────────────────┘            │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

When Wake is triggered, the card smoothly expands to display the 5-stage progress stepper and real-time download telemetry:

```
┌──────────────────────────────────────────────────────────────┐
│  Stage: Downloading Models (Stage 3 of 5)                    │
│  [■■■■■■■■■■■■■■■■■■□□□□□□□□□□□□] 64% · ETA: 1m 24s         │
│  Speed: 420 MB/s · 16.2 GB / 25.4 GB (4/6 files)             │
│                                                              │
│  ▼ Console Logs (Click to collapse)                          │
│  [18:42:01] KIT_STAGE download                               │
│  [18:42:02] Fetching moss_image_sft_deband_int8...           │
└──────────────────────────────────────────────────────────────┘
```

---

### Phase 2: The Injected ComfyUI Overlay Pill

The exact millisecond the state reaches `ready`, the browser automatically transitions into the authentic ComfyUI Web Application. The gateway injects a lightweight, non-intrusive floating pill (`overlay.js`):

```
┌──────────────────────────────────────────────────────────────┐
│                     ComfyUI Web App                          │
│                                                              │
│        [ Full ComfyUI Node Canvas or App Mode View ]         │
│                                                              │
│                                                              │
│                                  ┌─────────────────────────┐ │
│                                  │ ⏱ 54m left │ App ⇄ Graph│ │
│                                  │  [+1h]  [⏻ Stop GPU]    │ │
│                                  └─────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

- **Countdown & Warning**: Displays remaining session time. When time drops below `WARN_BEFORE_MIN`, the pill pulses amber.
- **App ⇄ Graph Toggle**: Executes ComfyUI's native command `Comfy.ToggleLinear` (equivalent to Alt+M), switching instantly between the simplified prompt card view and the complete node graph.
- **Session Extension**: Clicking **+1h** calls `POST /_gate/extend` to grant additional session time up to `EXTEND_MAX_TIMES`.
- **Safe Release**: Clicking **⏻ Stop GPU** triggers `POST /_gate/stop`, immediately shutting down the VM and stopping Compute Unit billing.

---

## 2. Design System & Frontend Principles

- **Zero-FOUC Theme Architecture**: An inline script in `<head>` queries `localStorage.getItem("kit.theme")` or falls back to system dark mode preference before CSS parses. This completely eliminates theme flash.
- **WebGL Aurora Shader**: The fluid ambient aurora background is a vanilla JavaScript and WebGL implementation adapted from [React Bits](https://reactbits.dev) by **David Haz** (MIT License).
- **Interactive Spotlight**: Cards feature cursor-tracked radial glow that smoothly fades on mouse leave.
- **Symmetrical 180° Gradient Buttons**: Buttons use vertically symmetric gradients (`linear-gradient(180deg, #FBBF24 0%, #D97706 100%)`) with top inner bevel highlights, avoiding left-edge color asymmetry.
- **Mobile Responsive Design**: Clean breakpoint handling at 480px, collapsing headers into 38x38px square buttons and preventing text collisions.
- **Anti-Bot Meta Guard**: Injects `<meta name="robots" content="noindex, nofollow, noarchive, nosnippet">` to shield the private gateway from search engines.
