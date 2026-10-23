# Wake page and overlay

> [!NOTE]
> **Architecture Clarification**: The wake page exists strictly during cold-start, stopping, and sleeping states. Once the session reaches `ready`, the user interacts with the **genuine, full-featured ComfyUI Web Application** directly, with full access to ComfyUI's native frontend, extensions, custom node canvases, and the App mode interface.

## Wake page (`apps/gate/web/index.html` + `apps/gate/web/bg.js`)

Served on `/` whenever ComfyUI is not `ready` — same layout idea as a sleeping Hugging Face Space (one
truly centered card), with our own brand look (logo palette: ComfyUI `#211927`/`#F2FF59`, Colab `#F9AB00`/`#E8710A`).
No build step: plain HTML/CSS/JS, static files served from `/_gate/static/`.

- **Zero-FOUC Theme Script**: Synchronously executed in `<head>` before CSS parses to guarantee instant dark/light match with zero layout flash.
- **Background**: animated WebGL aurora (`bg.js`, a vanilla port of React Bits *Aurora*) over a faint masked grid;
  it intensifies while waking. No WebGL → plain background. `prefers-reduced-motion` → one static frame, no CSS animations.
- **Card** (SpotlightCard-style cursor glow, glass blur): state badge, live viewer count (without distracting pulsing dot), ShinyText title.
- **Profile selector**: shadcn-style dismissible popover with click-outside transparent backdrop and `Escape` key close.
- **Language selector**: popover dropdown with `[✓ 简体中文, English]`.
- **Theme & GitHub**: 38x38px glassmorphic square icon buttons for instantaneous theme toggle and GitHub repository navigation.
- **Wake** button: 180° vertically symmetrical gradient with top highlight edge. The session is shared: once anyone wakes it every open
  page switches to **Starting…** (disabled) via the SSE stream (`/_gate/events`); a second click just joins.
- **Waking**: stage stepper (allocate → install → download → boot → smoke test) with per-stage elapsed time,
  an overall progress bar with ETA (download stage uses `KIT_PROGRESS` GB/speed, other stages use typical durations),
  and download detail (GB, files, MB/s).
- **Stop** (owners only, see `OWNER_ONLY_ACTIONS`); refusal reasons (`quiet_hours`, budget) shown inline.
- **CU** tiles: today / month / balance.
- **Console**: collapsible monospace log, last 400 boot-log lines, live and pre-injected in SSR for zero-delay display.

When the state becomes `ready` the card fades and the page reloads into ComfyUI (the overlay then opens the
default workflow in App mode).

Credits: Aurora / ShinyText / StarBorder / SpotlightCard effects adapted from [React Bits](https://reactbits.dev)
(MIT, © David Haz).

## Overlay (`apps/gate/web/overlay.js`)

Injected into ComfyUI's `index.html` by the proxy. A small pill shows:

- time left in the session (and the `session_ending` warning);
- **App ⇄ Graph** toggle — runs ComfyUI's own `Comfy.ToggleLinear` command (same as Alt+M);
- **+1h** (`/_gate/extend`) and **⏻** stop (owner only).

On first load it opens the profile's default workflow (`/kit/info` from the kit's heartbeat node) in the
view set by `DEFAULT_VIEW` (`app` by default). App mode shows the inputs/outputs declared in the profile's
`app:` block (`extra.linearData`).

## Design language

The wake page uses a dark glassmorphism style with tech-flavored motion adapted from [React Bits](https://reactbits.dev), ported to vanilla JS and CSS (no build step). The layout follows the Hugging Face Space sleeping page: one centered card that tells you the state and offers one action.

### Principles

- **One screen, one action.** A centered card shows the state, the profile selector and the Wake button. Nothing else competes with it.
- **The state is always visible.** Sleeping, waking, ready and failed each have a distinct look. Everyone who opens the page sees the same shared session state.
- **Motion has a purpose.** Background motion is slow and ambient. Fast motion is used only for feedback, such as button glow, the progress sweep and the status pulse.
- **Dark first, light supported.** Dark is the default. A light theme is available through the toggle (`data-theme=light`).

### Color tokens

Colors are CSS variables on `:root` in `gate/web/index.html`. They come from the logo palette, which uses Colab orange and amber with a neon-lime accent and a plum base.

| Token | Dark | Role |
|---|---|---|
| `--bg` | `#0E0A12` | Page background (plum black) |
| `--card` | `rgba(33,25,39,.72)` | Glass card fill |
| `--fg` / `--muted` | `#F4F1F6` / `#a49cab` | Text and secondary text |
| `--y` / `--o` | `#F9AB00` / `#E8710A` | Primary accents (amber, orange) |
| `--n` | `#F2FF59` | Neon highlight and glow |
| `--a0..--a2` | orange, amber, `#7B4BA8` | Aurora gradient stops |
| `--ok` / `--bad` | `#3ddc84` / `#ff6b4a` | Ready and failed states |

The light theme overrides the same tokens. Add new colors as tokens, never as inline values.

### Surfaces

- **Glass card:** translucent fill, `backdrop-filter: blur(18px) saturate(140%)`, a 1px hairline border (`--line2`), a soft deep shadow and an inner top highlight.
- **Hairlines:** 1px white borders at 9–16% opacity (`--line`, `--line2`) separate the sections.
- **Corners:** large radius on the card, 10px on the icon buttons.

### Typography

- The system UI font stack is used for all text.
- `--mono` (SF Mono, JetBrains Mono, Menlo) is used for logs, numbers, ETA and speed.
- The title uses a shimmer gradient (React Bits *Shiny Text*, the `shine` keyframes).

### Motion (React Bits ports)

| Effect | Where | Implementation |
|---|---|---|
| Aurora | Full-page background | `gate/web/bg.js` canvas with the `--a0..--a2` stops |
| Spotlight card | Main card | Radial glow follows the pointer via `--mx` / `--my` |
| Shiny text | Title | `shine` keyframes |
| Glow button | Wake button | Neon `--glow` shadow on hover and while starting |
| Status pulse | State dot | `pulse` keyframes |
| Progress sweep | Progress bar | `sweep` keyframes |

All motion respects `prefers-reduced-motion`.

### Components

- **Stepper:** 5 steps from GPU assignment to ready, plus an overall progress bar, ETA and download speed.
- **Log drawer:** collapsed by default, monospace, auto-scrolls.
- **Profile selector:** chooses the config for this wake. It is disabled while a session is waking or running.

### Responsive

On narrow screens the card fills the width with smaller padding, and controls stay large enough to tap.

## State fields for download progress

`GET /_gate/status` and every SSE `state` / `progress` event carry `progress.download` (absent until the download
stage starts). Byte counts are measured on the VM (finished files + bytes written by in-flight downloads), so the
bar moves smoothly instead of jumping per file.

| Field | Type | Meaning |
|---|---|---|
| `progress.download.bytes_done` | int | bytes on disk for all planned files |
| `progress.download.bytes_total` | int | planned bytes (sum of `size_gb` in the profile) |
| `progress.download.core_bytes_done` | int | same, core files only (what boot waits for) |
| `progress.download.core_bytes_total` | int | core planned bytes |
| `progress.download.bytes_per_s` | int | smoothed rate (EMA over 5 s samples) |
| `progress.download.eta_s` | int \| null | `(bytes_total - bytes_done) / bytes_per_s`; null while the rate is ~0 |
| `progress.download.files_done` / `files_total` | int | file counts |
| `progress.download.core_done` | bool | all core files present (ComfyUI can start) |
| `progress.download.paused` | bool | background downloads paused because a prompt is executing |
| `progress.download.restarts` | int | files restarted by the stall watchdog (no bytes for 45 s) |
| `progress.download.done_gb` / `total_gb` | float | legacy, file-granular |
| `models.on_demand` | string[] | installed workflows whose models are not on the VM yet |
| `models.fetching` | string[] | workflows being fetched now (`POST /_gate/fetch`) |
| `models.fetch_error` | string | last on-demand fetch error, `""` if none |

While booting, show `core_bytes_done / core_bytes_total` (that is what gates readiness). After `ready`, an
on-demand fetch (`models.fetching` non-empty) reuses `progress.download.*` for its own files.
A workflow listed in `models.on_demand` should offer "Download models (N GB)" → `POST /_gate/fetch
{"workflows": [id]}`; sizes come from `GET /_gate/profiles` (`workflows[].size_gb`).
