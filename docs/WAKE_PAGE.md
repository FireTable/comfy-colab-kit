# Wake page and overlay

## Wake page (`gate/web/index.html` + `gate/web/bg.js`)

Served on `/` whenever ComfyUI is not `ready` — same layout idea as a sleeping Hugging Face Space (one
truly centered card), with our own brand look (logo palette: ComfyUI `#211927`/`#F2FF59`, Colab `#F9AB00`/`#E8710A`).
No build step: plain HTML/CSS/JS, static files served from `/_gate/static/`.

- **Background**: animated WebGL aurora (`bg.js`, a vanilla port of React Bits *Aurora*) over a faint masked grid;
  it intensifies while waking. No WebGL → plain background. `prefers-reduced-motion` → one static frame, no CSS animations.
- **Card** (SpotlightCard-style cursor glow, glass blur): state badge, live viewer count, ShinyText title.
- **Profile selector** listing `GET /_gate/profiles` (title, workflow count, default download size, accelerator).
- **Wake** button (StarBorder-style) → `POST /_gate/wake`. The session is shared: once anyone wakes it every open
  page switches to **Starting…** (disabled) via the SSE stream (`/_gate/events`); a second click just joins.
- **Waking**: stage stepper (allocate → install → download → boot → smoke test) with per-stage elapsed time,
  an overall progress bar with ETA (download stage uses `KIT_PROGRESS` GB/speed, other stages use typical durations),
  and download detail (GB, files, MB/s).
- **Stop** (owners only, see `OWNER_ONLY_ACTIONS`); refusal reasons (`quiet_hours`, budget) shown inline.
- **CU** tiles: today / month / balance.
- **Console**: collapsible monospace log, last 400 boot-log lines, live.
- Dark by default; ◐ toggles light (stored in `localStorage["kit.theme"]`). Mobile layout tested at 412 px.

When the state becomes `ready` the card fades and the page reloads into ComfyUI (the overlay then opens the
default workflow in App mode).

Credits: Aurora / ShinyText / StarBorder / SpotlightCard effects adapted from [React Bits](https://reactbits.dev)
(MIT, © David Haz).

## Overlay (`gate/web/overlay.js`)

Injected into ComfyUI's `index.html` by the proxy. A small pill shows:

- time left in the session (and the `session_ending` warning);
- **App ⇄ Graph** toggle — runs ComfyUI's own `Comfy.ToggleLinear` command (same as Alt+M);
- **+1h** (`/_gate/extend`) and **⏻** stop (owner only).

On first load it opens the profile's default workflow (`/kit/info` from the kit's heartbeat node) in the
view set by `DEFAULT_VIEW` (`app` by default). App mode shows the inputs/outputs declared in the profile's
`app:` block (`extra.linearData`).
