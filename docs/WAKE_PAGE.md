# Wake page and overlay

## Wake page (`gate/web/index.html`)

Served on `/` whenever ComfyUI is not `ready` — the same idea as a sleeping Hugging Face Space.

- **Status card** with the current state, the boot stages (allocate → install → download → boot → smoke test)
  and a download bar fed by `KIT_PROGRESS` events.
- **Profile selector** listing `GET /_gate/profiles` (title, workflow count, default download size).
- **Wake** button → `POST /_gate/wake`. Because the session is shared, once anyone wakes it every open page
  switches to **Starting…** (disabled) through the SSE stream; a second click just joins.
- **Stop** (owners only, see `OWNER_ONLY_ACTIONS`) and a refusal banner for `quiet_hours` / budget reasons.
- **Logs**: the last 400 lines of the boot log, live.
- Light / dark theme toggle; palette taken from the logo.

The page reloads itself into ComfyUI when the state becomes `ready`.

## Overlay (`gate/web/overlay.js`)

Injected into ComfyUI's `index.html` by the proxy. A small pill shows:

- time left in the session (and the `session_ending` warning);
- **App ⇄ Graph** toggle — runs ComfyUI's own `Comfy.ToggleLinear` command (same as Alt+M);
- **+1h** (`/_gate/extend`) and **⏻** stop (owner only).

On first load it opens the profile's default workflow (`/kit/info` from the kit's heartbeat node) in the
view set by `DEFAULT_VIEW` (`app` by default). App mode shows the inputs/outputs declared in the profile's
`app:` block (`extra.linearData`).
