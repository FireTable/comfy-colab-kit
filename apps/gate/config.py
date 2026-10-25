"""Settings loaded from environment variables (.env). Non-secret values only;
secrets are always referenced through *_FILE paths and read lazily."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time as dtime
from pathlib import Path
from typing import Mapping

ACTIONS = ("wake", "switch", "stop", "extend")


class ConfigError(ValueError):
    pass


def _csv(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def _int(env: Mapping[str, str], key: str, default: int, minimum: int = 0) -> int:
    raw = (env.get(key) or "").strip()
    if raw == "":
        return default
    try:
        val = int(raw)
    except ValueError as e:
        raise ConfigError(f"{key} must be an integer, got {raw!r}") from e
    if val < minimum:
        raise ConfigError(f"{key} must be >= {minimum}, got {val}")
    return val


def _float(env: Mapping[str, str], key: str, default: float, minimum: float = 0.0) -> float:
    raw = (env.get(key) or "").strip()
    if raw == "":
        return default
    try:
        val = float(raw)
    except ValueError as e:
        raise ConfigError(f"{key} must be a number, got {raw!r}") from e
    if val < minimum:
        raise ConfigError(f"{key} must be >= {minimum}, got {val}")
    return val


def _bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = (env.get(key) or "").strip().lower()
    if raw == "":
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{key} must be true/false, got {raw!r}")


def parse_quiet_hours(raw: str | None) -> tuple[dtime, dtime] | None:
    """'01:00-08:00' -> (01:00, 08:00). Empty -> None. Window may wrap midnight."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        a, b = raw.split("-")
        ha, ma = (int(x) for x in a.strip().split(":"))
        hb, mb = (int(x) for x in b.strip().split(":"))
        start, end = dtime(ha, ma), dtime(hb, mb)
    except Exception as e:  # noqa: BLE001
        raise ConfigError(f"QUIET_HOURS must look like HH:MM-HH:MM, got {raw!r}") from e
    if start == end:
        raise ConfigError("QUIET_HOURS start and end must differ")
    return start, end


def in_quiet_hours(window: tuple[dtime, dtime] | None, now: dtime) -> bool:
    if not window:
        return False
    start, end = window
    if start < end:
        return start <= now < end
    return now >= start or now < end  # wraps midnight


@dataclass(frozen=True)
class AutoStop:
    idle_timeout_min: int = 30
    max_session_hours: float = 6.0
    extend_max_times: int = 1
    extend_hours: float = 1.0
    warn_before_min: int = 10
    boot_timeout_min: int = 40
    stop_on_page_close_min: int = 5
    quiet_hours: tuple[dtime, dtime] | None = None
    daily_cu_budget: float = 12.0
    monthly_cu_budget: float = 150.0
    min_cu_balance: float = 5.0
    cu_per_hour: float = 1.71
    orphan_sweep_min: int = 5
    watchdog_gate_lost_min: int = 20
    wake_on_visit: bool = False


@dataclass(frozen=True)
class Settings:
    public_host: str = "comfy.example.com"
    colab_tunnel_host: str = "comfy-colab.example.com"
    cf_access_team_domain: str = ""
    cf_access_aud: str = ""
    allowed_emails: tuple[str, ...] = ()
    owner_emails: tuple[str, ...] = ()
    owner_only_actions: tuple[str, ...] = ("wake", "stop", "switch", "extend")
    owner_only_control: bool = True
    auth_disabled: bool = False
    cf_service_token_file: str = ""
    cf_tunnel_token_file: str = ""
    hf_token_file: str = ""
    agent_token_file: str = ""
    colab_bin: str = "colab"
    colab_token_dir: str = ""
    colab_accelerator: str = "L4"
    colab_session_name: str = "comfy"
    profiles: tuple[str, ...] = ("profiles/examples/moss-image", "profiles/examples/sd-latest", "profiles/examples/qwen-image")
    default_profile: str = "moss-image"
    default_view: str = "app"
    fast_start: bool = True
    download_concurrency: int = 4
    #: parallel HTTP Range connections per model file (see packages/colab/setup.py DL_SCRIPT)
    download_connections: int = 8
    #: "disk" keeps the models on the VM disk, "ram" puts them in a tmpfs (faster, uses ~25 GB RAM)
    model_store: str = "disk"
    xet_high_performance: bool = True
    pip_installer: str = "uv"
    enable_smoke_test: bool = False
    listen_host: str = "0.0.0.0"
    listen_port: int = 8080
    state_dir: str = "/data/state"
    log_level: str = "info"
    auto_stop: AutoStop = field(default_factory=AutoStop)

    @property
    def backend_url(self) -> str:
        return f"https://{self.colab_tunnel_host}"

    @staticmethod
    def from_env(env: Mapping[str, str] | None = None) -> "Settings":
        env = dict(os.environ if env is None else env)
        owner_only_control = _bool(env, "OWNER_ONLY_CONTROL", True)
        default_actions = "wake,stop,switch,extend" if owner_only_control else "switch,stop,extend"
        owner_only_list = [a.lower() for a in _csv(env.get("OWNER_ONLY_ACTIONS", default_actions))]
        if owner_only_control:
            if "wake" not in owner_only_list:
                owner_only_list.append("wake")
            if "stop" not in owner_only_list:
                owner_only_list.append("stop")
        owner_only = tuple(owner_only_list)
        bad = [a for a in owner_only if a not in ACTIONS]
        if bad:
            raise ConfigError(f"OWNER_ONLY_ACTIONS has unknown actions {bad}; allowed: {ACTIONS}")
        view = (env.get("DEFAULT_VIEW") or "app").strip().lower()
        if view not in ("app", "graph"):
            raise ConfigError("DEFAULT_VIEW must be 'app' or 'graph'")
        listen = (env.get("LISTEN_ADDR") or "0.0.0.0:8080").strip()
        host, _, port = listen.rpartition(":")
        if not host or not port.isdigit():
            raise ConfigError(f"LISTEN_ADDR must be host:port, got {listen!r}")
        tunnel_host = (env.get("COLAB_TUNNEL_HOST") or env.get("COLAB_BACKEND_HOST") or "comfy-colab.example.com").strip()
        auto = AutoStop(
            idle_timeout_min=_int(env, "IDLE_TIMEOUT_MIN", 30),
            max_session_hours=_float(env, "MAX_SESSION_HOURS", 6.0, 0.1),
            extend_max_times=_int(env, "EXTEND_MAX_TIMES", 1),
            extend_hours=_float(env, "EXTEND_HOURS", 1.0, 0.1),
            warn_before_min=_int(env, "WARN_BEFORE_MIN", 10),
            boot_timeout_min=_int(env, "BOOT_TIMEOUT_MIN", 40, 1),
            stop_on_page_close_min=_int(env, "STOP_ON_PAGE_CLOSE_MIN", 0),
            quiet_hours=parse_quiet_hours(env.get("QUIET_HOURS")),
            daily_cu_budget=_float(env, "DAILY_CU_BUDGET", 12.0),
            monthly_cu_budget=_float(env, "MONTHLY_CU_BUDGET", 150.0),
            min_cu_balance=_float(env, "MIN_CU_BALANCE", 5.0),
            cu_per_hour=_float(env, "CU_PER_HOUR", 1.71, 0.01),
            orphan_sweep_min=_int(env, "ORPHAN_SWEEP_MIN", 5),
            watchdog_gate_lost_min=_int(env, "WATCHDOG_GATE_LOST_MIN", 20),
            wake_on_visit=_bool(env, "WAKE_ON_VISIT", False),
        )
        s = Settings(
            public_host=(env.get("PUBLIC_HOST") or "comfy.example.com").strip(),
            colab_tunnel_host=tunnel_host,
            cf_access_team_domain=(env.get("CF_ACCESS_TEAM_DOMAIN") or "").strip(),
            cf_access_aud=(env.get("CF_ACCESS_AUD") or "").strip(),
            allowed_emails=tuple(e.lower() for e in _csv(env.get("ALLOWED_EMAILS"))),
            owner_emails=tuple(e.lower() for e in _csv(env.get("OWNER_EMAILS"))),
            owner_only_actions=owner_only,
            owner_only_control=owner_only_control,
            auth_disabled=_bool(env, "AUTH_DISABLED", False),
            cf_service_token_file=(env.get("CF_SERVICE_TOKEN_FILE") or "").strip(),
            cf_tunnel_token_file=(env.get("CF_TUNNEL_TOKEN_FILE") or "").strip(),
            hf_token_file=(env.get("HF_TOKEN_FILE") or "").strip(),
            agent_token_file=(env.get("AGENT_TOKEN_FILE") or "").strip(),
            colab_bin=(env.get("COLAB_BIN") or "colab").strip(),
            colab_token_dir=(env.get("COLAB_TOKEN_DIR") or "").strip(),
            colab_accelerator=(env.get("COLAB_ACCELERATOR") or "L4").strip(),
            colab_session_name=(env.get("COLAB_SESSION_NAME") or "comfy").strip(),
            profiles=tuple(_csv(env.get("PROFILES"))) or Settings.profiles,
            default_profile=(env.get("DEFAULT_PROFILE") or "moss-image").strip(),
            default_view=view,
            fast_start=_bool(env, "FAST_START", True),
            download_concurrency=_int(env, "DOWNLOAD_CONCURRENCY", 4, 1),
            download_connections=_int(env, "DOWNLOAD_CONNECTIONS", 8, 1),
            model_store=(env.get("MODEL_STORE") or "disk").strip().lower(),
            xet_high_performance=_bool(env, "XET_HIGH_PERFORMANCE", True),
            pip_installer="pip" if (env.get("PIP_INSTALLER") or "uv").strip().lower() == "pip" else "uv",
            enable_smoke_test=_bool(env, "ENABLE_SMOKE_TEST", False),
            listen_host=host,
            listen_port=int(port),
            state_dir=(env.get("STATE_DIR") or "/data/state").strip(),
            log_level=(env.get("LOG_LEVEL") or "info").strip(),
            auto_stop=auto,
        )
        if not s.auth_disabled and not (s.cf_access_team_domain and s.cf_access_aud):
            raise ConfigError("CF_ACCESS_TEAM_DOMAIN and CF_ACCESS_AUD are required (or AUTH_DISABLED=true for local dev only)")
        return s


def read_secret_file(path: str) -> str:
    """Read a mounted secret; never log the value."""
    if not path:
        return ""
    p = Path(path)
    return p.read_text().strip() if p.is_file() else ""
