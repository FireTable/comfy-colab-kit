#!/usr/bin/env python3
"""Generate the architecture diagram SVG for comfy-colab-kit.

One layout definition, two language variants. Every edge endpoint is derived
from the geometry of the boxes it connects, so arrows always land on the
target border and a layout tweak never desynchronises the wiring.

Usage:
    python3 scripts/build-architecture-svg.py

Outputs:
    assets/architecture/flow.svg      (English)
    assets/architecture/flow.zh.svg   (简体中文)
"""
from __future__ import annotations

import base64
import sys
from dataclasses import dataclass, field
from pathlib import Path

# The geometry (edge routing, arrow markers) lives in the svg-illustration skill so it is
# shared with every diagram we author, not just this one.
_SKILL_LIB = Path.home() / ".claude/skills/svg-illustration/scripts"
sys.path.insert(0, str(_SKILL_LIB))
try:
    from diagram_geometry import (  # type: ignore
        Port,
        Rect,
        Side,
        arrow_marker,
        edge_attrs,
        route,
        route_bus,
    )
except ImportError:  # pragma: no cover - the skill may be absent
    print("warning: svg-illustration skill not found; falling back to inline geometry",
          file=sys.stderr)
    raise

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "assets" / "architecture"
HERO = OUT_DIR / "hero.webp"

CANVAS_W = 1180
CANVAS_H = 1100

# ── palette (mirrors assets/logo + apps/site/styles.css) ────────────────────
AMBER = "#f9ab00"
LIME = "#f2ff59"
ORANGE = "#e8710a"
PLUM = "#211927"

# ── arrow geometry ─────────────────────────────────────────────────────────
# markerUnits=userSpaceOnUse keeps the head a fixed pixel size instead of
# scaling it with stroke-width; refX sits exactly on the triangle tip so the
# tip lands on the path endpoint, i.e. on the target border.
ARROW_W = 11
ARROW_GAP = 3.0     # breathing room between the source border and the line
ARROW_HEAD = 11     # gap left for the head so the tip, not the base, touches


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def left(self) -> float:
        return self.x

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def bottom(self) -> float:
        return self.y + self.h


@dataclass(frozen=True)
class State:
    id: str
    x: float
    y: float
    w: float
    h: float
    kind: str            # boot | ready | failed | stopping | switching | idle
    label: str

    @property
    def box(self) -> Box:
        return Box(self.x, self.y, self.w, self.h)

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


@dataclass
class Lang:
    subtitle: str
    badge: str
    headline: str
    sec1: str
    sec1_note: str
    client_agent: str
    client_agent_sub: str
    client_browser: str
    client_browser_sub: str
    colab_note: str
    colab_cost: str
    sec2: str
    sec2_note: str
    fail_note: str
    stop_note: str
    switch_note: str
    billing_note: str
    sec3: str
    sec3_note: str
    props_title: str
    title_attr: str
    cf_items: list = field(default_factory=list)
    gate_items: list = field(default_factory=list)
    colab_items: list = field(default_factory=list)
    gates: list = field(default_factory=list)
    props: list = field(default_factory=list)
    legend: list = field(default_factory=list)


EN = Lang(
    subtitle="An always-available, wake-on-demand ComfyUI on Google Colab",
    badge="Zero-GPU idle · single shared instance · automatic shutdown",
    headline="From “press Wake” to “GPU released”: request path, 10-state lifecycle, five shutdown gates",
    sec1="① Request path",
    sec1_note="Agents and browsers travel the very same gateway path",
    client_agent="AI Agent",
    client_agent_sub="MCP / REST",
    client_browser="Browser",
    client_browser_sub="SSE / WS",
    cf_items=["Zero Trust · RS256 JWT", "Email OTP / OAuth / service token", "Cf-Access-Jwt-Assertion"],
    gate_items=["Auth · owner decision", "10-state machine", "SSE progress broadcast", "CU ledger · budget gate",
                "Reverse proxy /prompt · /history · /view · /ws"],
    colab_items=["Models 24.9 GB", "Workflows · nodes", "ComfyUI :8188", "Outputs output/"],
    colab_note="watchdog · self-destruct after 20 min without heartbeat",
    colab_cost="Billing 1.71 CU/h",
    sec2="② State machine",
    sec2_note="10-state lifecycle · dashed lines are failure fallbacks",
    fail_note="any boot state fails → failed → retry allocating",
    stop_note="stop is allowed from any non-sleeping state → VM released",
    switch_note="switch, cold change",
    billing_note="BILLING_STATES · five boot states + ready + switching + failed — a Colab instance exists here and is billed at 1.71 CU/h",
    sec3="③ Automatic shutdown",
    sec3_note="Five gates · any one of them releases the GPU and its CU",
    gates=[
        ("Idle timeout", "IDLE_TIMEOUT_MIN = 30", "skipped while the queue is busy", AMBER),
        ("Session limit", "MAX_SESSION_HOURS = 6", "extendable once, +1 h", ORANGE),
        ("CU budget", "12 / 150 CU per day / month", "wake refused below 5 CU", "#7b4ba8"),
        ("Watchdog", "WATCHDOG_GATE_LOST_MIN = 20", "VM self-destructs if the gate goes quiet", "#0ea5e9"),
        ("Explicit stop", "POST /_gate/stop", "agents release it as soon as they are done", "#3ddc84"),
    ],
    props_title="Three properties that matter",
    props=[
        ("Single instance, idempotent", "A concurrent wake joins the same in-flight boot instead of starting a second VM.", "Session.lock"),
        ("State is persisted", "A gateway restart re-attaches to the session and replays logs and progress.", "STATE_DIR/session.json"),
        ("Secrets never leave the gate", "The Colab token stays on the gateway, travels over stdin, and never touches disk.", "secrets/ · *_FILE pointers"),
    ],
    legend=["running", "booting", "failed", "stopping", "switching"],
    title_attr="Comfy Colab Kit: request path, 10-state lifecycle, and auto-stop gates",
)

ZH = Lang(
    subtitle="按需唤醒的 ComfyUI 网关 · 从请求到算力回收的完整链路",
    badge="Zero-GPU idle · 单实例幂等 · 自动停机",
    headline="从「按下唤醒」到「GPU 释放」：请求链路、10 态生命周期与五道停机闸门",
    sec1="① 请求链路",
    sec1_note="Agent 与浏览器走同一条网关路径",
    client_agent="AI Agent",
    client_agent_sub="MCP / REST",
    client_browser="浏览器",
    client_browser_sub="SSE / WS",
    cf_items=["Zero Trust · RS256 JWT", "邮箱 OTP / OAuth / 服务令牌", "Cf-Access-Jwt-Assertion"],
    gate_items=["鉴权 · owner 判定", "10 态状态机", "SSE 进度广播", "CU 账本 · 预算闸",
                "反向代理 /prompt · /history · /view · /ws"],
    colab_items=["模型 24.9 GB", "工作流 · 节点", "ComfyUI :8188", "出图 output/"],
    colab_note="watchdog · 心跳 20 min 自毁",
    colab_cost="计费中 1.71 CU/h",
    sec2="② 状态机",
    sec2_note="10 态生命周期 · 虚线为异常回退路径",
    fail_note="任一开机态失败 → failed → retry allocating",
    stop_note="任一非睡眠态均可 stop → 释放 VM",
    switch_note="switch 冷切换",
    billing_note="计费区间 BILLING_STATES · 五个开机态 + ready + switching + failed —— 此区间内 Colab 实例存在，按 1.71 CU/小时 计费",
    sec3="③ 自动停机",
    sec3_note="五道闸门 · 任意一条命中即释放 GPU 与 CU",
    gates=[
        ("空闲超时", "IDLE_TIMEOUT_MIN = 30", "队列非空时跳过", AMBER),
        ("会话上限", "MAX_SESSION_HOURS = 6", "可 extend 一次 +1h", ORANGE),
        ("CU 预算", "日 12 / 月 150 CU", "余额低于 5 CU 拒绝开机", "#7b4ba8"),
        ("看门狗", "WATCHDOG_GATE_LOST_MIN = 20", "网关失联 VM 自毁", "#0ea5e9"),
        ("显式停机", "POST /_gate/stop", "Agent 完成即释放", "#3ddc84"),
    ],
    props_title="三条关键性质",
    props=[
        ("单实例幂等", "并发唤醒只会加入同一个 in-flight 启动，不新开 VM。", "Session.lock"),
        ("状态持久化", "网关重启后接管会话并回放日志与进度。", "STATE_DIR/session.json"),
        ("密钥不出网关", "Colab token 仅存网关，经 stdin 传给内核，不落盘。", "secrets/ · *_FILE 指针"),
    ],
    legend=["运行中", "开机中", "异常", "停机中", "切换中"],
    title_attr="Comfy Colab Kit 请求链路、10 态生命周期与自动停机闸门",
)


# ── service logos (official vector paths, inlined so the SVG stays self-contained) ──
# cloudflare / googlecolab / huggingface: Simple Icons (CC0-1.0)
# comfyui: comfy.org/favicon.svg
LOGO_PATHS = {
    "cloudflare": (
        "M16.5088 16.8447c.1475-.5068.0908-.9707-.1553-1.3154-.2246-.3164-.6045-.499-1.0615-.5205l-8.6592-.1123a.1559.1559 0 0 1-.1333-.0713c-.0283-.042-.0351-.0986-.021-.1553.0278-.084.1123-.1484.2036-.1562l8.7359-.1123c1.0351-.0489 2.1601-.8868 2.5537-1.9136l.499-1.3013c.0215-.0561.0293-.1128.0147-.168-.5625-2.5463-2.835-4.4453-5.5499-4.4453-2.5039 0-4.6284 1.6177-5.3876 3.8614-.4927-.3658-1.1187-.5625-1.794-.499-1.2026.119-2.1665 1.083-2.2861 2.2856-.0283.31-.0069.6128.0635.894C1.5683 13.171 0 14.7754 0 16.752c0 .1748.0142.3515.0352.5273.0141.083.0844.1475.1689.1475h15.9814c.0909 0 .1758-.0645.2032-.1553l.12-.4268zm2.7568-5.5634c-.0771 0-.1611 0-.2383.0112-.0566 0-.1054.0415-.127.0976l-.3378 1.1744c-.1475.5068-.0918.9707.1543 1.3164.2256.3164.6055.498 1.0625.5195l1.8437.1133c.0557 0 .1055.0263.1329.0703.0283.043.0351.1074.0214.1562-.0283.084-.1132.1485-.204.1553l-1.921.1123c-1.041.0488-2.1582.8867-2.5527 1.914l-.1406.3585c-.0283.0713.0215.1416.0986.1416h6.5977c.0771 0 .1474-.0489.169-.126.1122-.4082.1757-.837.1757-1.2803 0-2.6025-2.125-4.727-4.7344-4.727"
    ),
    "googlecolab": (
        "M16.9414 4.9757a7.033 7.033 0 0 0-4.9308 2.0646 7.033 7.033 0 0 0-.1232 9.8068l2.395-2.395a3.6455 3.6455 0 0 1 5.1497-5.1478l2.397-2.3989a7.033 7.033 0 0 0-4.8877-1.9297zM7.07 4.9855a7.033 7.033 0 0 0-4.8878 1.9316l2.3911 2.3911a3.6434 3.6434 0 0 1 5.0227.1271l1.7341-2.9737-.0997-.0802A7.033 7.033 0 0 0 7.07 4.9855zm15.0093 2.1721l-2.3892 2.3911a3.6455 3.6455 0 0 1-5.1497 5.1497l-2.4067 2.4068a7.0362 7.0362 0 0 0 9.9456-9.9476zM1.932 7.1674a7.033 7.033 0 0 0-.002 9.6816l2.397-2.397a3.6434 3.6434 0 0 1-.004-4.8916zm7.664 7.4235c-1.38 1.3816-3.5863 1.411-5.0168.1134l-2.397 2.395c2.4693 2.3328 6.263 2.5753 9.0072.5455l.1368-.1115z"
    ),
    "huggingface": (
        "M12.025 1.13c-5.77 0-10.449 4.647-10.449 10.378 0 1.112.178 2.181.503 3.185.064-.222.203-.444.416-.577a.96.96 0 0 1 .524-.15c.293 0 .584.124.84.284.278.173.48.408.71.694.226.282.458.611.684.951v-.014c.017-.324.106-.622.264-.874s.403-.487.762-.543c.3-.047.596.06.787.203s.31.313.4.467c.15.257.212.468.233.542.01.026.653 1.552 1.657 2.54.616.605 1.01 1.223 1.082 1.912.055.537-.096 1.059-.38 1.572.637.121 1.294.187 1.967.187.657 0 1.298-.063 1.921-.178-.287-.517-.44-1.041-.384-1.581.07-.69.465-1.307 1.081-1.913 1.004-.987 1.647-2.513 1.657-2.539.021-.074.083-.285.233-.542.09-.154.208-.323.4-.467a1.08 1.08 0 0 1 .787-.203c.359.056.604.29.762.543s.247.55.265.874v.015c.225-.34.457-.67.683-.952.23-.286.432-.52.71-.694.257-.16.547-.284.84-.285a.97.97 0 0 1 .524.151c.228.143.373.388.43.625l.006.04a10.3 10.3 0 0 0 .534-3.273c0-5.731-4.678-10.378-10.449-10.378M8.327 6.583a1.5 1.5 0 0 1 .713.174 1.487 1.487 0 0 1 .617 2.013c-.183.343-.762-.214-1.102-.094-.38.134-.532.914-.917.71a1.487 1.487 0 0 1 .69-2.803m7.486 0a1.487 1.487 0 0 1 .689 2.803c-.385.204-.536-.576-.916-.71-.34-.12-.92.437-1.103.094a1.487 1.487 0 0 1 .617-2.013 1.5 1.5 0 0 1 .713-.174m-10.68 1.55a.96.96 0 1 1 0 1.921.96.96 0 0 1 0-1.92m13.838 0a.96.96 0 1 1 0 1.92.96.96 0 0 1 0-1.92M8.489 11.458c.588.01 1.965 1.157 3.572 1.164 1.607-.007 2.984-1.155 3.572-1.164.196-.003.305.12.305.454 0 .886-.424 2.328-1.563 3.202-.22-.756-1.396-1.366-1.63-1.32q-.011.001-.02.006l-.044.026-.01.008-.03.024q-.018.017-.035.036l-.032.04a1 1 0 0 0-.058.09l-.014.025q-.049.088-.11.19a1 1 0 0 1-.083.116 1.2 1.2 0 0 1-.173.18q-.035.029-.075.058a1.3 1.3 0 0 1-.251-.243 1 1 0 0 1-.076-.107c-.124-.193-.177-.363-.337-.444-.034-.016-.104-.008-.2.022q-.094.03-.216.087-.06.028-.125.063l-.13.074q-.067.04-.136.086a3 3 0 0 0-.135.096 3 3 0 0 0-.26.219 2 2 0 0 0-.12.121 2 2 0 0 0-.106.128l-.002.002a2 2 0 0 0-.09.132l-.001.001a1.2 1.2 0 0 0-.105.212q-.013.036-.024.073c-1.139-.875-1.563-2.317-1.563-3.203 0-.334.109-.457.305-.454m.836 10.354c.824-1.19.766-2.082-.365-3.194-1.13-1.112-1.789-2.738-1.789-2.738s-.246-.945-.806-.858-.97 1.499.202 2.362c1.173.864-.233 1.45-.685.64-.45-.812-1.683-2.896-2.322-3.295s-1.089-.175-.938.647 2.822 2.813 2.562 3.244-1.176-.506-1.176-.506-2.866-2.567-3.49-1.898.473 1.23 2.037 2.16c1.564.932 1.686 1.178 1.464 1.53s-3.675-2.511-4-1.297c-.323 1.214 3.524 1.567 3.287 2.405-.238.839-2.71-1.587-3.216-.642-.506.946 3.49 2.056 3.522 2.064 1.29.33 4.568 1.028 5.713-.624m5.349 0c-.824-1.19-.766-2.082.365-3.194 1.13-1.112 1.789-2.738 1.789-2.738s.246-.945.806-.858.97 1.499-.202 2.362c-1.173.864.233 1.45.685.64.451-.812 1.683-2.896 2.322-3.295s1.089-.175.938.647-2.822 2.813-2.562 3.244 1.176-.506 1.176-.506 2.866-2.567 3.49-1.898-.473 1.23-2.037 2.16c-1.564.932-1.686 1.178-1.464 1.53s3.675-2.511 4-1.297c.323 1.214-3.524 1.567-3.287 2.405.238.839 2.71-1.587 3.216-.642.506.946-3.49 2.056-3.522 2.064-1.29.33-4.568 1.028-5.713-.624"
    ),
    "comfyui": (
        "M31.0126 30.4797C31.0576 30.3275 31.0822 30.1671 31.0822 29.9985C31.0822 29.0649 30.3294 28.3081 29.4006 28.3081H21.8643C21.4593 28.3122 21.1279 27.9832 21.1279 27.576C21.1279 27.5019 21.1401 27.432 21.1565 27.3662L23.1858 20.259C23.2717 19.9465 23.5581 19.7161 23.8936 19.7161L31.4586 19.7079C33.0542 19.7079 34.4003 18.6262 34.8053 17.1497L35.9427 13.1889C35.9795 13.0491 36 12.8969 36 12.7447C36 11.8152 35.2513 11.0625 34.3266 11.0625H25.1742C23.5868 11.0625 22.2448 12.136 21.8316 13.5961L21.0624 16.2983C20.9724 16.6068 20.6901 16.833 20.3546 16.833H18.1575C16.5823 16.833 15.2526 17.8859 14.8271 19.3295L12.0614 29.0402C12.0205 29.1841 12 29.3404 12 29.4967C12 30.4304 12.7528 31.1871 13.6816 31.1871H15.8418C16.2468 31.1871 16.5782 31.5162 16.5782 31.9275C16.5782 31.9974 16.5701 32.0673 16.5496 32.1331L15.7845 34.8107C15.7477 34.9546 15.7232 35.1027 15.7232 35.2549C15.7232 36.1844 16.4719 36.937 17.3965 36.937L26.553 36.9288C28.1446 36.9288 29.4865 35.8512 29.8957 34.3829L31.0085 30.4838L31.0126 30.4797Z"
    ),
}

LOGO_STYLE = {
    # name: (fill, viewBox, size)
    # Logos sit on bright brand bands, so they use the deep plum ink for contrast
    # (same rule as label text on a colour block).
    "cloudflare": ("#211927", "0 0 24 24", 17),
    "googlecolab": ("#211927", "0 0 24 24", 18),
    "huggingface": ("#ffd21e", "0 0 24 24", 12),
    "comfyui": ("#211927", "0 0 48 48", 15),
}


# ── layout (single source of truth) ─────────────────────────────────────────
HERO_BOX = Box(700, 30, 440, 248)

CHIP_A = Box(40, 310, 150, 52)
CHIP_B = Box(40, 372, 150, 52)
CF = Box(240, 302, 182, 100)
GATE = Box(476, 288, 266, 148)
COLAB = Box(782, 288, 244, 148)

RAIL = Box(40, 506, 1100, 172)

STATES: list[State] = [
    State("sleeping", 64, 564, 104, 36, "idle", "sleeping"),
    State("allocating", 206, 564, 104, 36, "boot", "allocating"),
    State("installing", 348, 564, 104, 36, "boot", "installing"),
    State("downloading", 490, 564, 110, 36, "boot", "downloading"),
    State("booting", 638, 564, 96, 36, "boot", "booting"),
    State("smoke_testing", 772, 564, 118, 36, "boot", "smoke_testing"),
    State("ready", 928, 564, 96, 36, "ready", "ready"),
    State("failed", 628, 642, 96, 34, "failed", "failed"),
    State("stopping", 790, 642, 104, 34, "stopping", "stopping"),
    State("switching", 952, 642, 120, 34, "switching", "switching"),
]

STATE_EDGES = [
    ("sleeping", "allocating", "wake"),
    ("allocating", "installing", "setup"),
    ("installing", "downloading", "24.9GB"),
    ("downloading", "booting", "tunnel"),
    ("booting", "smoke_testing", "smoke"),
    ("smoke_testing", "ready", "serve"),
]

STATE_COLORS = {
    "idle": ("var(--bg-raise)", "var(--ink-secondary)", "var(--ink-muted)"),
    "boot": ("var(--yellow)", "var(--ink-on-tape)", None),
    "ready": ("var(--green)", "var(--ink-on-tape)", None),
    "failed": ("var(--rose)", "var(--ink-on-tape)", None),
    "stopping": ("var(--blue)", "var(--ink-on-tape)", None),
    "switching": ("var(--violet)", "var(--ink-on-tape)", None),
}

GATE_ROW_Y = 772
GATE_H = 74
GATE_W = 212
GATE_GAP = 12

PROP_Y = 882
PROP_H = 88

LEGEND_Y = 1044


def esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def h_edge(a: Box, b: Box, y: float | None = None) -> tuple[str, float, float]:
    """Horizontal edge: right edge of a → left edge of b. Returns (path, x1, x2)."""
    yy = a.cy if y is None else y
    x1 = a.right + ARROW_GAP
    x2 = b.left - ARROW_GAP
    return f"M{x1:.1f} {yy:.1f} H{x2:.1f}", x1, x2


def esc_len(t: str) -> float:
    """Rough advance width of a label, used to place edge captions."""
    return sum(1.0 if ord(c) > 0x2E80 else 0.56 for c in t)


def _polyline(points) -> str:
    """Plain polyline path data."""
    return "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in points)


def caption(add, x: float, y: float, text: str, size: float = 9.5,
         anchor: str = "start", chip: str = "var(--bg-card)") -> None:
    """Caption with a background chip so a routed line can pass underneath it."""
    w = esc_len(text) * size * 0.56 + 10
    h = size + 6
    rx = x - w / 2 if anchor == "middle" else x - 5
    add(f'    <rect x="{rx:.1f}" y="{y - size:.1f}" width="{w:.1f}" height="{h:.1f}" rx="4" fill="{chip}" opacity="0.92" />')
    add(f'    <text x="{x:.1f}" y="{y:.1f}" font-size="{size}" class="code-text" fill="var(--ink-muted)" '
        f'text-anchor="{anchor}">{esc(text)}</text>')


def build(lang: Lang, hero_b64: str) -> str:
    o: list[str] = []
    add = o.append

    add(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {CANVAS_W} {CANVAS_H}" '
        f'width="100%" height="100%" role="img" aria-label="{esc(lang.title_attr)}">')
    add("""  <title>""" + esc(lang.title_attr) + "</title>")
    add("  <defs>")
    add("""    <style>
      :root {
        --bg-canvas: #faf8f5;
        --bg-card: #ffffff;
        --bg-raise: #f5f1ea;
        --border-card: #e7e5e4;
        --ink-primary: #1c1917;
        --ink-secondary: #78716c;
        --ink-muted: #a8a29e;
        --amber: #f9ab00;
        --lime: #f2ff59;
        --orange: #e8710a;
        --plum: #211927;
        --green: #86efac;
        --blue: #93c5fd;
        --rose: #fda4af;
        --yellow: #fde047;
        --violet: #c4b5fd;
        --mint: #3ddc84;
        --sky: #0ea5e9;
        --ink-on-tape: #0f172a;
        --grid-color: rgba(28, 25, 23, 0.055);
      }
      @media (prefers-color-scheme: dark) {
        :root {
          --bg-canvas: #141416;
          --bg-card: #1c1c1f;
          --bg-raise: #242428;
          --border-card: #2e2e32;
          --ink-primary: #f5f5f4;
          --ink-secondary: #a8a29e;
          --ink-muted: #78716c;
          --amber: #f2ff59;
          --orange: #f9ab00;
          --grid-color: rgba(255, 255, 255, 0.055);
        }
      }
      text {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
        text-rendering: geometricPrecision;
        -webkit-font-smoothing: antialiased;
      }
      .code-text {
        font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
      }
      @keyframes travel {
        from { transform: translateX(0); }
        to   { transform: translateX(var(--len)); }
      }
      @keyframes pulse { 0%,100% { opacity: .35; } 50% { opacity: .9; } }
      @keyframes breathe { 0%,100% { opacity: .85; } 50% { opacity: 1; } }
      .mover {
        animation: travel 2.2s linear infinite;
        will-change: transform;
      }
      .mover-b { animation-duration: 2.9s; animation-delay: -0.9s; }
      .mover-c { animation-duration: 2.4s; animation-delay: -1.5s; }
      .mover-d { animation-duration: 3.4s; animation-delay: -2.1s; }
      .pulse { animation: pulse 2.6s ease-in-out infinite; }
      .breathe { animation: breathe 3.4s ease-in-out infinite; }
    </style>""")
    add("""    <pattern id="dot-grid" width="22" height="22" patternUnits="userSpaceOnUse">
      <circle cx="2" cy="2" r="1.2" fill="var(--grid-color)" />
    </pattern>""")
    # ── markers: fixed pixel size, tip exactly on the path endpoint ──
    add("    " + arrow_marker("arrow-amber", fill="var(--amber)", size=ARROW_W))
    add("    " + arrow_marker("arrow-plum", fill="var(--ink-muted)", size=ARROW_W))
    add(f"""    <linearGradient id="g-amber" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{AMBER}" /><stop offset="1" stop-color="{ORANGE}" />
    </linearGradient>""")
    add(f"""    <linearGradient id="g-lime" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="{LIME}" /><stop offset="1" stop-color="{AMBER}" />
    </linearGradient>""")
    for name, path in LOGO_PATHS.items():
        fill, viewbox, _size = LOGO_STYLE[name]
        add(f'    <symbol id="logo-{name}" viewBox="{viewbox}"><path fill="{fill}" d="{path}" /></symbol>')
    add(f"""    <clipPath id="clip-hero"><rect x="{HERO_BOX.x}" y="{HERO_BOX.y}" width="{HERO_BOX.w}" height="{HERO_BOX.h}" rx="14" /></clipPath>""")
    add("  </defs>")

    # ── canvas ──
    add(f'  <rect width="{CANVAS_W}" height="{CANVAS_H}" fill="var(--bg-canvas)" />')
    add(f'  <rect width="{CANVAS_W}" height="{CANVAS_H}" fill="url(#dot-grid)" />')

    # ── header ──
    add("""  <g transform="translate(40,34)">
    <rect x="0" y="0" width="6" height="46" rx="3" fill="url(#g-amber)" />""")
    add(f'    <text x="20" y="22" font-size="23" font-weight="700" fill="var(--ink-primary)">Comfy Colab Kit</text>')
    add(f'    <text x="20" y="44" font-size="14" fill="var(--ink-secondary)">{esc(lang.subtitle)}</text>')
    add("  </g>")
    add("""  <g transform="translate(40,100)">
    <rect x="0" y="0" width="288" height="30" rx="15" fill="var(--bg-raise)" stroke="var(--border-card)" stroke-width="1.2" />
    <circle cx="17" cy="15" r="5" fill="var(--mint)" class="pulse" />""")
    add(f'    <text x="30" y="20" font-size="12.5" fill="var(--ink-secondary)">{esc(lang.badge)}</text>')
    add("  </g>")
    add(f'  <text x="40" y="162" font-size="12.5" fill="var(--ink-muted)">{esc(lang.headline)}</text>')

    # ── hero image ──
    add("""  <g>
    <image x="700" y="30" width="440" height="248" clip-path="url(#clip-hero)"
           preserveAspectRatio="xMidYMid slice" href="data:image/webp;base64,""" + hero_b64 + """" />""")
    add('    <rect x="700" y="30" width="440" height="248" rx="14" fill="none" stroke="var(--border-card)" stroke-width="1.2" />')
    add("  </g>")

    # ── section 1 ──
    add("""  <g transform="translate(40,274)">
    <rect x="0" y="0" width="126" height="24" rx="4" fill="var(--yellow)" opacity="0.9" />""")
    add(f'    <text x="63" y="17" font-size="12.5" font-weight="700" fill="var(--ink-on-tape)" text-anchor="middle">{esc(lang.sec1)}</text>')
    add(f'    <text x="140" y="17" font-size="12.5" fill="var(--ink-muted)">{esc(lang.sec1_note)}</text>')
    add("  </g>")

    # client chips
    for box, title, sub, accent in ((CHIP_A, lang.client_agent, lang.client_agent_sub, "var(--blue)"),
                                    (CHIP_B, lang.client_browser, lang.client_browser_sub, "var(--green)")):
        add(f"""  <g transform="translate({box.x},{box.y})">
    <rect x="0" y="0" width="{box.w}" height="{box.h}" rx="10" fill="var(--bg-card)" stroke="var(--border-card)" stroke-width="1.4" />
    <rect x="0" y="0" width="4" height="{box.h}" rx="2" fill="{accent}" />""")
        add(f'    <text x="16" y="22" font-size="13.5" font-weight="650" fill="var(--ink-primary)">{esc(title)}</text>')
        add(f'    <text x="16" y="40" font-size="11" class="code-text" fill="var(--ink-secondary)">{esc(sub)}</text>')
        add("  </g>")

    # Cloudflare Access card
    add(f"""  <g transform="translate({CF.x},{CF.y})">
    <rect x="0" y="6" width="{CF.w}" height="{CF.h}" rx="12" fill="#000000" opacity="0.07" />
    <rect x="0" y="0" width="{CF.w}" height="{CF.h}" rx="12" fill="var(--bg-card)" stroke="var(--border-card)" stroke-width="1.4" />
    <rect x="0" y="0" width="{CF.w}" height="26" rx="12" fill="url(#g-lime)" opacity="0.92" />
    <rect x="0" y="16" width="{CF.w}" height="10" fill="url(#g-lime)" opacity="0.92" />""")
    add(f'    <use href="#logo-cloudflare" x="10" y="4" width="17" height="17" />')
    add(f'    <text x="32" y="18" font-size="12.5" font-weight="700" fill="var(--ink-on-tape)">Cloudflare Access</text>')
    for i, line in enumerate(lang.cf_items):
        cls = ' class="code-text"' if i == 2 else ""
        size = 9.5 if i == 2 else 11
        add(f'    <text x="14" y="{46 + i * 18}" font-size="{size}"{cls} fill="var(--ink-secondary)">{esc(line)}</text>')
    add("  </g>")

    # comfy-gate card
    add(f"""  <g transform="translate({GATE.x},{GATE.y})">
    <rect x="0" y="10" width="{GATE.w}" height="{GATE.h}" rx="14" fill="#000000" opacity="0.08" />
    <rect x="0" y="0" width="{GATE.w}" height="{GATE.h}" rx="14" fill="var(--bg-card)" stroke="var(--border-card)" stroke-width="1.6" />
    <rect x="0" y="0" width="{GATE.w}" height="30" rx="14" fill="var(--plum)" />
    <rect x="0" y="18" width="{GATE.w}" height="12" fill="var(--plum)" />""")
    add(f'    <text x="16" y="21" font-size="13" font-weight="700" fill="{AMBER}">comfy-gate</text>')
    add('    <text x="116" y="21" font-size="10.5" class="code-text" fill="#a8a29e">aiohttp · :8080</text>')
    inner = lang.gate_items
    add("""    <g transform="translate(14,42)">""")
    cells = [(0, 0, 118, inner[0]), (126, 0, 122, inner[1]),
             (0, 38, 118, inner[2]), (126, 38, 122, inner[3])]
    for cx, cy, cw, txt in cells:
        add(f'      <rect x="{cx}" y="{cy}" width="{cw}" height="30" rx="7" fill="var(--bg-raise)" stroke="var(--border-card)" stroke-width="1" />')
        add(f'      <text x="{cx + cw / 2}" y="{cy + 19}" font-size="11" text-anchor="middle" fill="var(--ink-primary)">{esc(txt)}</text>')
    add(f'      <rect x="0" y="76" width="248" height="30" rx="7" fill="var(--bg-raise)" stroke="var(--border-card)" stroke-width="1" />')
    add(f'      <text x="124" y="95" font-size="10.5" text-anchor="middle" fill="var(--ink-primary)">{esc(inner[4])}</text>')
    add("    </g>")
    add("  </g>")

    # Colab card
    add(f"""  <g transform="translate({COLAB.x},{COLAB.y})">
    <rect x="0" y="10" width="{COLAB.w}" height="{COLAB.h}" rx="14" fill="#000000" opacity="0.08" />
    <rect x="0" y="0" width="{COLAB.w}" height="{COLAB.h}" rx="14" fill="var(--bg-card)" stroke="var(--border-card)" stroke-width="1.6" />
    <rect x="0" y="0" width="{COLAB.w}" height="30" rx="14" fill="url(#g-amber)" />
    <rect x="0" y="18" width="{COLAB.w}" height="12" fill="url(#g-amber)" />""")
    add(f'    <use href="#logo-googlecolab" x="11" y="5" width="18" height="18" />')
    add(f'    <text x="35" y="21" font-size="13" font-weight="700" fill="var(--ink-on-tape)">Colab L4 Runtime</text>')
    inner = lang.colab_items
    add("""    <g transform="translate(14,40)">
      <rect x="0" y="0" width="216" height="112" rx="10" fill="var(--bg-raise)" stroke="var(--border-card)" stroke-width="1" />
      <rect x="0" y="0" width="216" height="22" rx="10" fill="var(--bg-canvas)" opacity="0.6" />""")
    add('      <text x="10" y="15" font-size="10.5" class="code-text" fill="var(--ink-secondary)">/content · 易失</text>')
    add('      <rect x="152" y="4" width="17" height="14" rx="3" fill="var(--plum)"/>')
    add('      <use href="#logo-huggingface" x="154" y="5" width="12" height="12" />')
    chips = [(0, 30, inner[0], "var(--rose)"), (102, 30, inner[1], "var(--blue)"),
             (0, 56, inner[2], "var(--green)"), (102, 56, inner[3], "var(--yellow)")]
    for cx, cy, txt, col in chips:
        add(f'      <rect x="{cx}" y="{cy}" width="94" height="20" rx="5" fill="{col}" opacity="0.9" />')
        add(f'      <text x="{cx + 47}" y="{cy + 14}" font-size="9.5" text-anchor="middle" fill="var(--ink-on-tape)">{esc(txt)}</text>')
    # ComfyUI 品牌标记压在 ComfyUI 芯片上
    add('      <rect x="0" y="56" width="94" height="20" rx="5" fill="var(--green)" opacity="0.9" />')
    add('      <use href="#logo-comfyui" x="4" y="58" width="16" height="16" />')
    add('      <text x="57" y="70" font-size="9.5" text-anchor="middle" fill="var(--ink-on-tape)">ComfyUI :8188</text>')
    add(f'      <text x="10" y="94" font-size="9.5" fill="var(--ink-muted)">{esc(lang.colab_note)}</text>')
    add(f'      <text x="10" y="106" font-size="9.5" class="code-text" fill="var(--ink-muted)">{esc(lang.colab_cost)}</text>')
    add("    </g>")
    add("  </g>")

    # ── request-path edges (derived from box geometry) ──
    add("  <!-- request path edges -->")
    movers: list[tuple[float, float, float, str]] = []
    for src, dst, y in ((CHIP_A, CF, CHIP_A.cy), (CF, GATE, CF.cy), (GATE, COLAB, GATE.cy)):
        ed = route(Rect(src.x, src.y, src.w, src.h), Rect(dst.x, dst.y, dst.w, dst.h))
        add(f'  <path d="{ed.to_path()}" {edge_attrs("arrow-amber", stroke="var(--amber)", width=2.2)} />')
        x1, x2 = ed.points[0][0], ed.points[-1][0]
        movers.append((x1, y, x2 - x1, "mover"))
    add(f'  <text x="{(CHIP_A.right + CF.left) / 2:.1f}" y="{CHIP_A.cy - 10:.1f}" font-size="10" class="code-text" '
        f'fill="var(--ink-muted)" text-anchor="middle">Bearer</text>')
    add(f'  <text x="{(GATE.right + COLAB.left) / 2:.1f}" y="{GATE.cy - 10:.1f}" font-size="9" class="code-text" '
        f'fill="var(--ink-muted)" text-anchor="middle">exec</text>')

    # ── section 2 ──
    add("""  <g transform="translate(40,470)">
    <rect x="0" y="0" width="126" height="24" rx="4" fill="var(--green)" opacity="0.9" />""")
    add(f'    <text x="63" y="17" font-size="12.5" font-weight="700" fill="var(--ink-on-tape)" text-anchor="middle">{esc(lang.sec2)}</text>')
    add(f'    <text x="140" y="17" font-size="12.5" fill="var(--ink-muted)">{esc(lang.sec2_note)}</text>')
    add("  </g>")

    add(f"""  <g>
    <rect x="{RAIL.x}" y="{RAIL.y}" width="{RAIL.w}" height="{RAIL.h}" rx="14" fill="var(--bg-card)" stroke="var(--border-card)" stroke-width="1.3" />""")

    # states
    add('    <g font-size="11.5" text-anchor="middle">')
    for st in STATES:
        fill, ink, stroke = STATE_COLORS[st.kind]
        extra = ""
        if st.kind == "idle":
            extra = f' stroke="{stroke}" stroke-width="1.5"'
        cls = ' class="breathe"' if st.kind == "ready" else ""
        add(f'      <g transform="translate({st.x},{st.y})"{cls}>')
        add(f'        <rect x="0" y="0" width="{st.w}" height="{st.h}" rx="9" fill="{fill}"{extra} />')
        add(f'        <text x="{st.w / 2}" y="{st.h / 2 + 5}" font-weight="{700 if st.kind == "ready" else 650}" fill="{ink}">{esc(st.label)}</text>')
        add("      </g>")
    add("    </g>")

    # boot chain edges
    add("    <!-- boot chain edges -->")
    by_id = {s.id: s for s in STATES}
    for a, b, cap in STATE_EDGES:
        sa, sb = by_id[a], by_id[b]
        ed = route(Rect(sa.x, sa.y, sa.w, sa.h), Rect(sb.x, sb.y, sb.w, sb.h))
        add(f'    <path d="{ed.to_path()}" {edge_attrs("arrow-amber", stroke="var(--amber)", width=2.2)} />')
        x1, x2 = ed.points[0][0], ed.points[-1][0]
        add(f'    <text x="{(x1 + x2) / 2:.1f}" y="{sa.y - 10}" font-size="9.5" class="code-text" fill="var(--ink-muted)" text-anchor="middle">{esc(cap)}</text>')
        movers.append((x1, sa.cy, x2 - x1, "mover"))

    # failure fallbacks
    add("    <!-- failure fallbacks -->")
    # fan-in: every boot state drops onto one trunk, one arrow enters `failed`
    boot_stubs = [st for st in STATES if st.kind == "boot"]
    failed_rect = Rect(by_id["failed"].x, by_id["failed"].y, by_id["failed"].w, by_id["failed"].h)
    trunk_y = min(st.box.bottom for st in boot_stubs) + 26
    for ed in route_bus([Rect(st.box.x, st.box.y, st.box.w, st.box.h) for st in boot_stubs],
                        failed_rect, source_side=Side.BOTTOM, target_side=Side.TOP):
        pts = list(ed.points)
        # the library routes each source to the target anchor; force a shared trunk line
        pts = [pts[0], (pts[0][0], trunk_y), (failed_rect.cx, trunk_y), (failed_rect.cx, failed_rect.top)]
        add(f'    <path d="{_polyline(pts)}" {edge_attrs("arrow-plum", width=1.5, dash="4 4")} />')
    # caption sits in the empty lower-left, clear of both the bus and the stop loop
    caption(add, 76, 666, lang.fail_note)

    # failed → stopping
    add(f'    <path d="M724 659 H790" stroke="var(--ink-muted)" stroke-width="1.6" fill="none" marker-end="url(#arrow-plum)" />')
    add(f'    <text x="757" y="654" font-size="9.5" class="code-text" fill="var(--ink-muted)" text-anchor="middle">stop</text>')

    # stop → sleeping
    add('    <path d="M842 676 V686 Q842 694 834 694 H128 Q120 694 120 686 V616 Q120 608 112 608 H100 V600" '
        'stroke="var(--ink-muted)" stroke-width="1.6" fill="none" marker-end="url(#arrow-plum)" />')
    caption(add, 470, 700, lang.stop_note, anchor="middle")

    # ready → switching: the endpoint is derived from the switching box, never typed
    sw = by_id["switching"]
    rd = by_id["ready"]
    sw_x = sw.x + sw.w / 2
    rd_x = rd.x + rd.w / 2
    add(f'    <path d="M{rd_x:.1f} {rd.box.bottom:.1f} V{rd.box.bottom + 16:.1f} '
        f'Q{rd_x:.1f} {rd.box.bottom + 24:.1f} {rd_x - 8:.1f} {rd.box.bottom + 24:.1f} '
        f'H{sw_x + 8:.1f} Q{sw_x:.1f} {rd.box.bottom + 24:.1f} {sw_x:.1f} {rd.box.bottom + 32:.1f} '
        f'V{sw.y:.1f}" '
        f'{edge_attrs("arrow-plum", width=1.5, dash="4 4")} />')
    caption(add, 1046, 614, lang.switch_note, anchor="middle")

    # billing band
    add(f"""    <g transform="translate(64,708)">
      <rect x="0" y="0" width="1046" height="22" rx="6" fill="var(--rose)" opacity="0.2" />
      <text x="523" y="15" font-size="10.5" text-anchor="middle" fill="var(--ink-secondary)">{esc(lang.billing_note)}</text>
    </g>""")
    add("  </g>")

    # ── section 3 ──
    add("""  <g transform="translate(40,736)">
    <rect x="0" y="0" width="126" height="24" rx="4" fill="var(--rose)" opacity="0.9" />""")
    add(f'    <text x="63" y="17" font-size="12.5" font-weight="700" fill="var(--ink-on-tape)" text-anchor="middle">{esc(lang.sec3)}</text>')
    add(f'    <text x="140" y="17" font-size="12.5" fill="var(--ink-muted)">{esc(lang.sec3_note)}</text>')
    add("  </g>")

    add(f'  <g transform="translate(40,{GATE_ROW_Y})" font-size="12">')
    for i, (title, code, note, accent) in enumerate(lang.gates):
        x = i * (GATE_W + GATE_GAP)
        w = GATE_W if i < len(lang.gates) - 1 else 204
        add(f'    <g transform="translate({x},0)">')
        add(f'      <rect x="0" y="0" width="{w}" height="{GATE_H}" rx="11" fill="var(--bg-card)" stroke="var(--border-card)" stroke-width="1.4" />')
        add(f'      <rect x="0" y="0" width="{w}" height="4" rx="2" fill="{accent}" />')
        add(f'      <text x="14" y="26" font-size="13" font-weight="650" fill="var(--ink-primary)">{esc(title)}</text>')
        add(f'      <text x="14" y="45" font-size="11.5" class="code-text" fill="var(--ink-secondary)">{esc(code)}</text>')
        add(f'      <text x="14" y="62" font-size="10.5" fill="var(--ink-muted)">{esc(note)}</text>')
        add("    </g>")
    add("  </g>")

    # ── properties ──
    add(f"""  <g transform="translate(40,{PROP_Y})">
    <rect x="0" y="0" width="1100" height="150" rx="14" fill="var(--bg-card)" stroke="var(--border-card)" stroke-width="1.3" />
    <text x="18" y="26" font-size="13" font-weight="700" fill="var(--ink-primary)">{esc(lang.props_title)}</text>""")
    for i, (title, body, code) in enumerate(lang.props):
        x = 18 + i * 362
        add(f'    <g transform="translate({x},42)">')
        add(f'      <rect x="0" y="0" width="340" height="88" rx="10" fill="var(--bg-raise)" />')
        dot = ("var(--amber)", "var(--orange)", "var(--mint)")[i]
        add(f'      <rect x="12" y="14" width="6" height="6" rx="3" fill="{dot}" />')
        add(f'      <text x="28" y="20" font-size="12.5" font-weight="650" fill="var(--ink-primary)">{esc(title)}</text>')
        add(f'      <text x="12" y="42" font-size="11" fill="var(--ink-secondary)">{esc(body)}</text>')
        add(f'      <text x="12" y="76" font-size="10.5" class="code-text" fill="var(--ink-muted)">{esc(code)}</text>')
        add("    </g>")
    add("  </g>")

    # ── legend ──
    add(f'  <g transform="translate(40,{LEGEND_Y})" font-size="10.5">')
    dots = [("var(--mint)", lang.legend[0]), ("var(--yellow)", lang.legend[1]),
            ("var(--rose)", lang.legend[2]), ("var(--blue)", lang.legend[3]),
            ("var(--violet)", lang.legend[4])]
    x = 0
    for col, name in dots:
        add(f'    <circle cx="{x + 5}" cy="-4" r="4" fill="{col}" />')
        add(f'    <text x="{x + 16}" y="0" fill="var(--ink-muted)">{esc(name)}</text>')
        x += 16 + int(esc_len(name) * 11) + 18
    add('    <path d="M400 -8 H428" stroke="var(--ink-muted)" stroke-width="1.5" stroke-dasharray="4 4" fill="none" />')
    add('    <text x="436" y="0" fill="var(--ink-muted)">failure path</text>')
    add('    <text x="1100" y="0" class="code-text" fill="var(--ink-muted)" text-anchor="end">comfy-colab-kit</text>')
    add("  </g>")

    # ── movers layer (transform-only animation) ──
    add("  <!-- flow movers: transform-only, GPU composited -->")
    add('  <g id="movers">')
    for x, y, ln, cls in movers:
        r = 3.0 if ln > 30 else 2.6
        halo = r * 2.1
        add(f'    <g transform="translate({x:.1f},{y:.1f})">')
        add(f'      <circle r="{halo:.1f}" fill="var(--amber)" class="{cls}" style="--len:{ln:.1f}px" opacity="0.2"/>')
        add(f'      <circle r="{r:.1f}" fill="var(--amber)" class="{cls}" style="--len:{ln:.1f}px"/>')
        add("    </g>")
    add("  </g>")

    add("</svg>")
    return "\n".join(o) + "\n"


def main() -> int:
    if not HERO.is_file():
        print(f"missing hero asset: {HERO}", file=sys.stderr)
        return 1
    hero_b64 = base64.b64encode(HERO.read_bytes()).decode()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for lang, name in ((EN, "flow.svg"), (ZH, "flow.zh.svg")):
        svg = build(lang, hero_b64)
        path = OUT_DIR / name
        path.write_text(svg, encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}  ({len(svg) // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
