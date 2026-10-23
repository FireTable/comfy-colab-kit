# Auto-stop, wake admission and CU budget

All rules live in `gate/stopper.py` as pure functions and are configured **only** through `.env`
(see `.env.example`). The gate evaluates them every 30 s (`Session.tick`) and stops the VM with
`colab stop` when one fires; the reason is logged and broadcast to every viewer.

## Wake admission (`admit_wake`)

A wake is refused with `409 <reason>` when:

| Reason | Rule | Variable |
|---|---|---|
| `quiet_hours` | local time inside the window (may wrap midnight, e.g. `23:30-07:00`) | `QUIET_HOURS`, `TZ` |
| `daily_budget` | CU used today ≥ budget | `DAILY_CU_BUDGET` (0 = off) |
| `monthly_budget` | CU used this month ≥ budget | `MONTHLY_CU_BUDGET` (0 = off) |
| `low_balance` | last `colab ccu-info` balance < minimum | `MIN_CU_BALANCE` |

## Stop rules (`evaluate`, in priority order)

| Reason | Fires when | Variable |
|---|---|---|
| `failed` | the session is in `failed` (VM may still exist) | — |
| `boot_timeout` | still booting N min after the wake | `BOOT_TIMEOUT_MIN` |
| `quiet_hours` / `daily_budget` / `monthly_budget` / `low_balance` | same rules as admission, applied to running sessions | as above |
| `max_session` | session age ≥ `MAX_SESSION_HOURS` + extensions × `EXTEND_HOURS` | `MAX_SESSION_HOURS`, `EXTEND_HOURS`, `EXTEND_MAX_TIMES` |
| `idle` | `ready`, ComfyUI queue empty and no HTTP / WebSocket activity for N min | `IDLE_TIMEOUT_MIN` (0 = off) |
| `page_closed` | `ready`, queue empty and no wake page / overlay open for N min | `STOP_ON_PAGE_CLOSE_MIN` (0 = off) |

`idle` and `page_closed` never fire while a prompt is running or queued.
When the remaining session time is ≤ `WARN_BEFORE_MIN`, status carries `warn: "session_ending"`; the overlay
shows it and offers **+1h** (`POST /_gate/extend`, at most `EXTEND_MAX_TIMES`).

## Other knobs

| Variable | Effect |
|---|---|
| `WAKE_ON_VISIT` | `true` = opening `/` while sleeping wakes the default profile (Hugging Face Space behaviour) |
| `ORPHAN_SWEEP_MIN` | how often the gate checks `colab sessions`; a VM that exists while the gate is sleeping is stopped |
| `WATCHDOG_GATE_LOST_MIN` | Colab side: if the gate's heartbeat (`/kit/heartbeat`) is absent for N min the VM releases itself (`runtime.unassign`) — protects against a dead VPS |
| `CU_PER_HOUR` | estimate used by the local ledger (`STATE_DIR/cu_ledger.json`) between balance refreshes |

## CU ledger

CU are accrued while the session is in a billing state (see [ARCHITECTURE.md](ARCHITECTURE.md#state-machine))
and before every stop. `GET /_gate/status` exposes `cu.today`, `cu.month`, `cu.balance` and `cu.per_hour`.
The ledger is an estimate; `colab ccu-info` is the source of truth for the balance.
