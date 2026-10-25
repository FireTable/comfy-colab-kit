#!/usr/bin/env python3
"""Generate the architecture diagram SVG for comfy-colab-kit.

The layout is computed, not typed. Graphviz (through `scripts/dot-layout.mjs`)
decides where every box goes and how every line reaches it; this file only
decides what a box looks like. Adding a state or resizing a card needs no
coordinate arithmetic — the wiring follows by itself.

One layout definition, two language variants.

Usage:
    python3 scripts/build-architecture-svg.py

Outputs:
    assets/architecture/flow.svg      (English)
    assets/architecture/flow.zh.svg   (简体中文)
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "assets" / "architecture"
DOT_HELPER = ROOT / "scripts" / "dot-layout.mjs"

# ── palette (mirrors assets/logo + apps/site/styles.css) ────────────────────
AMBER = "#f9ab00"
LIME = "#f2ff59"
ORANGE = "#e8710a"
PLUM = "#211927"

# ── canvas ──────────────────────────────────────────────────────────────────
CANVAS_W = 1180
MARGIN = 40
CONTENT_W = CANVAS_W - 2 * MARGIN

# ── arrow geometry ─────────────────────────────────────────────────────────
# markerUnits=userSpaceOnUse keeps the head a fixed pixel size instead of
# scaling it with stroke-width; refX sits exactly on the triangle tip so the
# tip lands on the path endpoint, i.e. on the target border.
ARROW_W = 11
MOVER_DUR = ("2.2s", "2.9s", "2.4s", "3.4s")  # period of each travelling light

# ── text metrics ───────────────────────────────────────────────────────────
# A conservative estimate of the advance width, used to wrap labels. The real
# width depends on the font the browser picks, so the layout checker measures
# the rendered result and fails the build if a line does not fit.
W_LATIN = 0.55
W_MONO = 0.60
W_CJK = 1.0


def text_width(s: str, size: float, mono: bool = False) -> float:
    units = sum(W_CJK if ord(c) > 0x2E80 else (W_MONO if mono else W_LATIN) for c in s)
    return units * size


def wrap(text: str, width: float, size: float, mono: bool = False) -> list[str]:
    """Greedy wrap so that no line is wider than ``width``."""
    lines: list[str] = []
    cur = ""
    for word in text.split(" "):
        trial = f"{cur} {word}".strip()
        if cur and text_width(trial, size, mono) > width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


# ── layout: Graphviz computes the geometry ─────────────────────────────────


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
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def top(self) -> float:
        return self.y


@dataclass
class Layout:
    """The result of one Graphviz run: where the boxes are, how the lines go."""

    width: float
    height: float
    nodes: dict = field(default_factory=dict)
    edges: list = field(default_factory=list)

    def box(self, node_id: str) -> Box:
        n = self.nodes[node_id]
        return Box(n["x"], n["y"], n["w"], n["h"])

    def edge(self, tail: str, head: str) -> str:
        for e in self.edges:
            if e["tail"] == tail and e["head"] == head:
                return e["d"]
        raise KeyError(f"no edge {tail}->{head}")

    def edge_midpoint(self, tail: str, head: str) -> tuple[float, float]:
        """Middle of a routed edge, so a label can sit on the line."""
        return _sample_path(self.edge(tail, head), 0.5)

    def shift_to(self, x: float) -> "Layout":
        """Move the layout so its leftmost box starts at ``x``.

        Graphviz leaves a little padding around the drawing, so the boxes do
        not start at zero; aligning on the boxes themselves keeps the row
        exactly as wide as the span that was asked for.
        """
        if not self.nodes:
            return self
        left = min(n["x"] for n in self.nodes.values())
        return self.offset(x - left, 0)

    def offset(self, dx: float, dy: float) -> "Layout":
        """Move the whole layout, boxes and lines together."""
        nodes = {k: {**v, "x": v["x"] + dx, "y": v["y"] + dy} for k, v in self.nodes.items()}
        edges = [{**e, "d": _translate_path(e["d"], dx, dy)} for e in self.edges]
        return Layout(self.width, self.height, nodes, edges)


def _sample_path(d: str, t: float) -> tuple[float, float]:
    """Evaluate an SVG path at parameter ``t`` (0..1) along its total length."""
    tokens = re.findall(r"[MLCQZ]|-?\d*\.?\d+", d)
    i = 0
    cur = (0.0, 0.0)
    segs: list[tuple] = []
    cmd = ""
    while i < len(tokens):
        tok = tokens[i]
        if tok.isalpha():
            cmd = tok
            i += 1
            continue
        if cmd in ("M", "L"):
            p = (float(tokens[i]), float(tokens[i + 1]))
            i += 2
            if cmd == "L":
                segs.append((cur, p, None, None))
            cur = p
        elif cmd == "C":
            c1 = (float(tokens[i]), float(tokens[i + 1]))
            c2 = (float(tokens[i + 2]), float(tokens[i + 3]))
            p = (float(tokens[i + 4]), float(tokens[i + 5]))
            i += 6
            segs.append((cur, p, c1, c2))
            cur = p
        elif cmd == "Q":
            c1 = (float(tokens[i]), float(tokens[i + 1]))
            p = (float(tokens[i + 2]), float(tokens[i + 3]))
            i += 4
            segs.append((cur, p, c1, None))
            cur = p
        else:
            i += 1
    if not segs:
        return cur
    lengths = []
    for a, b, c1, c2 in segs:
        if c1 is None:
            lengths.append(_dist(a, b))
        else:
            prev, total = a, 0.0
            for k in range(1, 11):
                pt = _cubic(a, c1, c2, b, k / 10)
                total += _dist(prev, pt)
                prev = pt
            lengths.append(total)
    total_len = sum(lengths) or 1.0
    want = total_len * t
    walked = 0.0
    for (a, b, c1, c2), length in zip(segs, lengths):
        if walked + length >= want:
            local = (want - walked) / length if length else 0.0
            if c1 is None:
                return (a[0] + (b[0] - a[0]) * local, a[1] + (b[1] - a[1]) * local)
            best = a
            for k in range(1, 21):
                pt = _cubic(a, c1, c2, b, k / 20)
                if _dist(a, pt) / max(_dist(a, b), 1e-6) >= local:
                    return pt
                best = pt
            return best
        walked += length
    return segs[-1][1]


def extend_to_border(d: str, box: Box) -> str:
    """Run a path out to the border of the box it points at.

    Graphviz stops the line short of the target and draws the arrowhead in
    the gap. This diagram draws its own arrowhead as a marker, so the line
    itself has to reach the border for the head to sit on it.
    """
    pts = _path_endpoints(d)
    if len(pts) < 2:
        return d
    (x0, y0), (x1, y1) = pts[-2], pts[-1]
    dx, dy = x1 - x0, y1 - y0
    length = (dx * dx + dy * dy) ** 0.5
    if length < 1e-6:
        return d
    dx, dy = dx / length, dy / length
    t = _ray_to_box(x1, y1, dx, dy, box)
    if t is None or t <= 0.1:
        return d
    return f"{d} L{x1 + dx * t:.2f} {y1 + dy * t:.2f}"


def _ray_to_box(px: float, py: float, dx: float, dy: float, box: Box) -> float | None:
    """Distance from a point to the border of a box along a direction."""
    tmin, tmax = 0.0, float("inf")
    for origin, direction, lo, hi in (
        (px, dx, box.left, box.right),
        (py, dy, box.top, box.bottom),
    ):
        if abs(direction) < 1e-9:
            if not lo <= origin <= hi:
                return None
            continue
        t1 = (lo - origin) / direction
        t2 = (hi - origin) / direction
        if t1 > t2:
            t1, t2 = t2, t1
        tmin = max(tmin, t1)
        tmax = min(tmax, t2)
        if tmin > tmax:
            return None
    return tmin if tmin > 0 else None


def _path_endpoints(d: str) -> list[tuple[float, float]]:
    """First and last coordinate pair of a path."""
    nums = [float(v) for v in re.findall(r"-?\d*\.?\d+", d)]
    pts = list(zip(nums[0::2], nums[1::2]))
    return [pts[0], pts[-1]] if len(pts) >= 2 else []


def _translate_path(d: str, dx: float, dy: float) -> str:
    """Shift every coordinate pair in a path."""
    i = 0

    def repl(m: re.Match) -> str:
        nonlocal i
        v = float(m.group(0))
        out = v + dx if i % 2 == 0 else v + dy
        i += 1
        return f"{out:.2f}"

    return re.sub(r"-?\d*\.\d+|-?\d+", repl, d)


def _cubic(p0, p1, p2, p3, t: float) -> tuple[float, float]:
    u = 1 - t
    x = u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0]
    y = u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]
    return (x, y)


def _dist(a, b) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def dot(dot_src: str) -> Layout:
    """Run one graph through Graphviz and read back the geometry."""
    if not DOT_HELPER.is_file():
        raise SystemExit(f"missing layout helper: {DOT_HELPER}")
    if shutil.which("node") is None:
        raise SystemExit("node is required for the layout step (npm install)")
    r = subprocess.run(
        ["node", str(DOT_HELPER)],
        input=json.dumps({"dot": dot_src}),
        capture_output=True, text=True, cwd=ROOT,
    )
    if r.returncode != 0:
        raise SystemExit(f"layout failed:\n{r.stderr}")
    data = json.loads(r.stdout)
    return Layout(data["width"], data["height"], data["nodes"], data["edges"])


def _layers(node_ids: list[str], edges: list[tuple[str, str]] | None) -> dict[str, int]:
    """Which column each node lands in.

    Two nodes that feed the same target share a column, so the count of
    columns is not the count of nodes. It is the number of layers in the
    graph, which is what decides how many gaps the row has.
    """
    if not edges:
        return {n: i for i, n in enumerate(node_ids)}
    layer = {n: 0 for n in node_ids}
    for _ in range(len(node_ids)):
        changed = False
        for a, b in edges:
            if layer[b] < layer[a] + 1:
                layer[b] = layer[a] + 1
                changed = True
        if not changed:
            break
    return layer


def dot_row(node_ids: list[str], sizes: dict[str, tuple[float, float]],
            span: float, edges: list[tuple[str, str]] | None = None,
            invisible: bool = False) -> str:
    """A DOT graph whose nodes are laid out left to right across ``span`` px.

    ``edges`` are real connectors. The invisible chain trick forces a row of
    independent cards into one line without drawing anything between them.
    """
    # a column is as wide as its widest node, and the row is as wide as the
    # sum of its columns — not the sum of every node in it
    layers = _layers(node_ids, edges)
    columns: dict[int, float] = {}
    for node, layer in layers.items():
        columns[layer] = max(columns.get(layer, 0.0), sizes[node][0])
    total = sum(columns.values())
    gap = max(0.05, (span - total) / max(1, len(columns) - 1))
    lines = [
        "digraph G {",
        f'  graph [rankdir=LR, splines=ortho, nodesep=0.22, ranksep={gap / 96:.4f}, margin=0, bgcolor="transparent"];',
        '  node [shape=box, fixedsize=true, margin="0", style="", label=""];',
    ]
    for n in node_ids:
        w, h = sizes[n]
        lines.append(f'  {n} [width={w / 96:.4f}, height={h / 96:.4f}];')
    if edges:
        for a, b in edges:
            lines.append(f"  {a} -> {b};")
    elif invisible:
        lines.append(f"  {' -> '.join(node_ids)} [style=invis];")
    lines.append("}")
    return "\n".join(lines)


# ── content: the declarative spec ──────────────────────────────────────────
#
# `assets/architecture/flow.spec.json` describes the diagram: which boxes
# exist, how big they are, how they are wired and what they say. This file
# only knows how to *draw* a box. Editing the picture means editing the spec.

SPEC_PATH = OUT_DIR / "flow.spec.json"


def load_spec() -> dict:
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


def say(value, lang: str) -> str:
    """A label from the spec: either a plain string or one per language."""
    if isinstance(value, dict):
        return value.get(lang, value.get("en", ""))
    return value


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
        "M12.025 1.13c-5.77 0-10.449 4.647-10.449 10.378 0 1.112.178 2.181.503 3.185.064-.222.203-.444.416-.577a.96.96 0 0 1 .524-.15c.293 0 .584.124.84.284.278.173.48.408.71.694.226.282.458.611.684.951v-.014c.017-.324.106-.622.264-.874s.403-.487.762-.543c.3-.047.596.06.787.203s.31.313.4.467c.15.257.212.468.233.542.01.026.653 1.552 1.657 2.54.616.605 1.01 1.223 1.082 1.912.055.537-.096 1.059-.38 1.572.637.121 1.294.187 1.967.187.657 0 1.298-.063 1.921-.178-.287-.517-.44-1.041-.384-1.581.07-.69.465-1.307 1.081-1.913 1.004-.987 1.647-2.513 1.657-2.539.021-.074.083-.285.233-.542.09-.154.208-.323.4-.467a1.08 1.08 0 0 1 .787-.203c.359.056.604.29.762.543s.247.55.265.874v.015c.225-.34.457-.67.683-.952.23-.286.432-.52.71-.694.257-.16.547-.284.84-.285a.97.97 0 0 1 .524.151c.228.143.373.388.43.625l.006.04a10.3 10.3 0 0 0 .534-3.273c0-5.731-4.678-10.378-10.449-10.378M8.327 6.583a1.5 1.5 0 0 1 .713.174 1.487 1.487 0 0 1 .617 2.013c-.183.343-.762-.214-1.102-.094-.38.134-.532.914-.917.71a1.487 1.487 0 0 1 .69-2.803m7.486 0a1.487 1.487 0 0 1 .689 2.803c-.385.204-.536-.576-.916-.71-.34-.12-.92.437-1.103.094a1.487 1.487 0 0 1 .617-2.013 1.5 1.5 0 0 1 .713-.174m-10.68 1.55a.96.96 0 1 1 0 1.921.96.96 0 1 1 0-1.92m13.838 0a.96.96 0 1 1 0 1.92.96.96 0 1 1 0-1.92M8.489 11.458c.588.01 1.965 1.157 3.572 1.164 1.607-.007 2.984-1.155 3.572-1.164.196-.003.305.12.305.454 0 .886-.424 2.328-1.563 3.202-.22-.756-1.396-1.366-1.63-1.32q-.011.001-.02.006l-.044.026-.01.008-.03.024q-.018.017-.035.036l-.032.04a1 1 0 0 0-.058.09l-.014.025q-.049.088-.11.19a.83.83 0 0 1-.083.116 1.2 1.2 0 0 1-.173.18q-.035.029-.075.058a1.3 1.3 0 0 1-.251-.243 1 1 0 0 1-.076-.107c-.124-.193-.177-.363-.337-.444-.034-.016-.104-.008-.2.022q-.094.03-.216.087-.06.028-.125.063l-.13.074q-.067.04-.136.086a3 3 0 0 0-.135.096 3 3 0 0 0-.26.219 2 2 0 0 0-.12.121 1 1 0 0 0-.106.128l-.002.002a1 1 0 0 0-.09.132l-.001.001a1.2 1.2 0 0 0-.105.212q-.013.036-.024.073c-1.139-.875-1.563-2.317-1.563-3.203 0-.334.109-.457.305-.454m.836 10.354c.824-1.19.766-2.082-.365-3.194-1.13-1.112-1.789-2.738-1.789-2.738s-.246-.945-.806-.858-.97 1.499.202 2.362c1.173.864-.233 1.45-.685.64-.45-.812-1.683-2.896-2.322-3.295s-1.089-.175-.938.647 2.822 2.813 2.562 3.244-1.176-.506-1.176-.506-2.866-2.567-3.49-1.898.473 1.23 2.037 2.16c1.564.932 1.686 1.178 1.464 1.53s-3.675-2.511-4-1.297c-.323 1.214 3.524 1.567 3.287 2.405-.238.839-2.71-1.587-3.216-.642-.506.946 3.49 2.056 3.522 2.064 1.29.33 4.568 1.028 5.713-.624m5.349 0c-.824-1.19-.766-2.082.365-3.194 1.13-1.112 1.789-2.738 1.789-2.738s.246-.945.806-.858.97 1.499-.202 2.362c-1.173.864.233 1.45.685.64.451-.812 1.683-2.896 2.322-3.295s1.089-.175.938.647-2.822 2.813-2.562 3.244 1.176-.506 1.176-.506 2.866 2.567 3.49 1.898-.473 1.23-2.037-2.16c-1.564.932-1.686 1.178-1.464 1.53s3.675 2.511 4 1.297c.323-1.214-3.524-1.567-3.287-2.405.238-.839 2.71 1.587 3.216.642.506-.946-3.49-2.056-3.522-2.064-1.29-.33-4.568-1.028-5.713.624"
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

# the accent a spec cell can ask for, and the colour it turns into
ACCENTS = {
    "amber": "var(--amber)", "orange": "var(--orange)", "violet": "var(--violet)",
    "sky": "var(--sky)", "mint": "var(--mint)", "blue": "var(--blue)",
    "green": "var(--green)", "rose": "var(--rose)", "yellow": "var(--yellow)",
}

STATE_COLORS = {
    "idle": ("var(--bg-raise)", "var(--ink-secondary)", True),
    "boot": ("var(--yellow)", "var(--ink-on-tape)", False),
    "ready": ("var(--green)", "var(--ink-on-tape)", False),
    "failed": ("var(--rose)", "var(--ink-on-tape)", False),
    "stopping": ("var(--blue)", "var(--ink-on-tape)", False),
    "switching": ("var(--violet)", "var(--ink-on-tape)", False),
}
STATE_KIND = {
    "sleeping": "idle", "allocating": "boot", "installing": "boot",
    "downloading": "boot", "booting": "boot", "smoke_testing": "boot",
    "ready": "ready", "failed": "failed", "stopping": "stopping",
    "switching": "switching",
}

def esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def arrow_marker(marker_id: str, fill: str, size: float = ARROW_W) -> str:
    """A marker whose tip sits exactly on the path endpoint.

    ``markerUnits="userSpaceOnUse"`` keeps the head a fixed pixel size, and
    ``refX`` is placed on the triangle's tip so the head's point — not its
    base — lands on the endpoint, i.e. on the target border.
    """
    inset = 0.9
    tip = size - inset
    return (
        f'<marker id="{marker_id}" viewBox="0 0 {size:.0f} {size:.0f}" '
        f'refX="{tip:.1f}" refY="{size / 2:.1f}" '
        f'markerWidth="{size:.0f}" markerHeight="{size:.0f}" '
        f'markerUnits="userSpaceOnUse" orient="auto">'
        f'<path d="M{inset:.1f} {inset:.1f} L{tip:.1f} {size / 2:.1f} '
        f'L{inset:.1f} {size - inset:.1f} Z" fill="{fill}" />'
        f"</marker>"
    )


def edge_attrs(marker_id: str | None = None, stroke: str = "var(--ink-muted)",
               width: float = 1.6, dash: str | None = None,
               opacity: float | None = None) -> str:
    parts = [f'stroke="{stroke}"', f'stroke-width="{width}"', 'fill="none"']
    if dash:
        parts.append(f'stroke-dasharray="{dash}"')
    if opacity is not None:
        parts.append(f'opacity="{opacity}"')
    if marker_id:
        parts.append(f'marker-end="url(#{marker_id})"')
    return " ".join(parts)


def hero_panel(add) -> None:
    """The illustration in the top right: a phone hands work to a GPU.

    It is drawn, not embedded. GitHub serves raw files with
    ``content-security-policy: default-src 'none'``, which blocks a ``data:``
    image inside an SVG that is opened directly — the embedded picture turns
    into a broken-image icon. Pure vector geometry survives every viewer.
    """
    add("""  <g transform="translate(700,30)">
    <rect x="0" y="0" width="440" height="248" rx="14" fill="url(#g-hero)" />
    <ellipse cx="230" cy="112" rx="150" ry="86" fill="url(#g-hero-glow)" />""")

    # the phone, seen from the back
    add("""    <g transform="translate(52,74)">
      <rect x="0" y="0" width="58" height="104" rx="12" fill="#2c2338" stroke="#6d5a80" stroke-width="1.4" />
      <rect x="6" y="12" width="46" height="76" rx="5" fill="#191322" />
      <rect x="21" y="5" width="16" height="3" rx="1.5" fill="#6d5a80" />
      <circle cx="29" cy="95" r="4" fill="none" stroke="#6d5a80" stroke-width="1.2" />
      <circle cx="29" cy="95" r="1.2" fill="#6d5a80" />
    </g>""")

    # the beam: a tapered ribbon in the brand gradient
    add('    <path d="M110 108 C168 50 250 50 306 100 L306 126 C250 80 168 80 110 124 Z" '
        'fill="url(#g-beam)" />')
    add('    <path d="M116 114 C172 64 246 64 300 104" fill="none" stroke="#fff3c4" '
        'stroke-width="1.5" stroke-linecap="round" opacity="0.5" />')

    # the accelerator card, tilted
    add("""    <g transform="translate(292,84)">
      <path d="M14 40 L96 8 L124 24 L42 56 Z" fill="#3b3049" stroke="#f9ab00" stroke-width="1.4" />
      <path d="M42 56 L124 24 L124 50 L42 82 Z" fill="#241c2f" stroke="#f9ab00" stroke-width="1.4" />
      <path d="M14 40 L42 56 L42 82 L14 66 Z" fill="#2b2236" stroke="#f9ab00" stroke-width="1.4" />
      <path d="M58 44 L92 28" fill="none" stroke="#f9ab00" stroke-width="1.1" opacity="0.6" />
      <path d="M62 56 L96 40" fill="none" stroke="#f9ab00" stroke-width="1.1" opacity="0.6" />
      <circle cx="34" cy="49" r="3" fill="#3ddc84" />
    </g>""")

    add('    <rect x="0" y="0" width="440" height="248" rx="14" fill="none" '
        'stroke="var(--border-card)" stroke-width="1.2" />')
    add("  </g>")


# ── drawing the boxes ──────────────────────────────────────────────────────


def band(w: float, h: float, r: float) -> str:
    """A header band: rounded at the top, square at the bottom.

    Drawn as one path rather than a rounded rect plus a square patch, so a
    geometry checker sees a single shape instead of two overlapping ones.
    """
    return (f"M0 {r:.0f} A{r:.0f} {r:.0f} 0 0 1 {r:.0f} 0 H{w - r:.0f} "
            f"A{r:.0f} {r:.0f} 0 0 1 {w:.0f} {r:.0f} V{h:.0f} H0 Z")


def draw_chip(add, box: Box, node: dict, lang: str) -> None:
    add(f"""  <g transform="translate({box.x:.1f},{box.y:.1f})">
    <rect x="0" y="0" width="{box.w:.0f}" height="{box.h:.0f}" rx="10" fill="var(--bg-card)" stroke="var(--border-card)" stroke-width="1.4" />
    <rect x="0" y="0" width="4" height="{box.h:.0f}" rx="2" fill="{ACCENTS[node["accent"]]}" />""")
    add(f'    <text x="16" y="22" font-size="13.5" font-weight="650" fill="var(--ink-primary)">{esc(say(node["label"], lang))}</text>')
    add(f'    <text x="16" y="40" font-size="11" class="code-text" fill="var(--ink-secondary)">{esc(say(node["sub"], lang))}</text>')
    add("  </g>")


def draw_cloudflare(add, box: Box, node: dict, lang: str) -> None:
    add(f"""  <g transform="translate({box.x:.1f},{box.y:.1f})">
    <rect x="0" y="6" width="{box.w:.0f}" height="{box.h:.0f}" rx="12" fill="#000000" opacity="0.07" />
    <rect x="0" y="0" width="{box.w:.0f}" height="{box.h:.0f}" rx="12" fill="var(--bg-card)" stroke="var(--border-card)" stroke-width="1.4" />
    <path d="{band(box.w, 26, 12)}" fill="url(#g-lime)" opacity="0.92" />""")
    add('    <use href="#logo-cloudflare" x="10" y="5" width="17" height="17" />')
    add(f'    <text x="32" y="18" font-size="12.5" font-weight="700" fill="var(--ink-on-tape)">{esc(say(node["label"], lang))}</text>')
    for i, line in enumerate(node["items"]):
        cls = ' class="code-text"' if i == 2 else ""
        size = 9.5 if i == 2 else 11
        add(f'    <text x="14" y="{50 + i * 20}" font-size="{size}"{cls} fill="var(--ink-secondary)">{esc(say(line, lang))}</text>')
    add("  </g>")


def draw_gateway(add, box: Box, node: dict, lang: str) -> None:
    add(f"""  <g transform="translate({box.x:.1f},{box.y:.1f})">
    <rect x="0" y="10" width="{box.w:.0f}" height="{box.h:.0f}" rx="14" fill="#000000" opacity="0.08" />
    <rect x="0" y="0" width="{box.w:.0f}" height="{box.h:.0f}" rx="14" fill="var(--bg-card)" stroke="var(--border-card)" stroke-width="1.6" />
    <path d="{band(box.w, 30, 14)}" fill="var(--plum)" />""")
    add('    <use href="#kit-glyph" x="10" y="4" width="24" height="24" />')
    add(f'    <text x="42" y="21" font-size="13" font-weight="700" fill="{AMBER}">{esc(say(node["label"], lang))}</text>')
    add(f'    <text x="128" y="21" font-size="10.5" class="code-text" fill="#a8a29e">{esc(node["sub"])}</text>')
    add("""    <g transform="translate(12,40)">""")
    for i, txt in enumerate(node["items"]):
        add(f'      <rect x="0" y="{i * 28}" width="242" height="25" rx="6" fill="var(--bg-raise)" stroke="var(--border-card)" stroke-width="1" />')
        add(f'      <text x="121" y="{i * 28 + 17}" font-size="11" text-anchor="middle" fill="var(--ink-primary)">{esc(say(txt, lang))}</text>')
    add("    </g>")
    add("  </g>")


def draw_colab(add, box: Box, node: dict, lang: str) -> None:
    add(f"""  <g transform="translate({box.x:.1f},{box.y:.1f})">
    <rect x="0" y="10" width="{box.w:.0f}" height="{box.h:.0f}" rx="14" fill="#000000" opacity="0.08" />
    <rect x="0" y="0" width="{box.w:.0f}" height="{box.h:.0f}" rx="14" fill="var(--bg-card)" stroke="var(--border-card)" stroke-width="1.6" />
    <path d="{band(box.w, 30, 14)}" fill="url(#g-amber)" />""")
    add('    <use href="#logo-googlecolab" x="11" y="7" width="18" height="18" />')
    add(f'    <text x="35" y="21" font-size="13" font-weight="700" fill="var(--ink-on-tape)">{esc(say(node["label"], lang))}</text>')
    add("""    <g transform="translate(14,40)">
      <rect x="0" y="0" width="216" height="112" rx="10" fill="var(--bg-raise)" stroke="var(--border-card)" stroke-width="1" />
      <rect x="0" y="0" width="216" height="22" rx="10" fill="var(--bg-canvas)" opacity="0.6" />""")
    add(f'      <text x="10" y="15" font-size="10.5" class="code-text" fill="var(--ink-secondary)">{esc(node["path"])}</text>')
    add('      <rect x="152" y="4" width="17" height="14" rx="3" fill="var(--plum)"/>')
    add('      <use href="#logo-huggingface" x="154" y="5" width="12" height="12" />')
    items = node["items"]
    # 两枚芯片等宽，中间留 8，两侧各留 10
    chips = [(10, 30, items[0], "var(--rose)"), (112, 30, items[1], "var(--blue)"),
             (10, 56, items[2], "var(--green)"), (112, 56, items[3], "var(--yellow)")]
    for cx, cy, txt, col in chips:
        add(f'      <rect x="{cx}" y="{cy}" width="94" height="20" rx="5" fill="{col}" opacity="0.9" />')
        if cx == 10 and cy == 56:
            # this chip carries the ComfyUI mark, so the label starts after it
            add('      <use href="#logo-comfyui" x="3" y="57" width="20" height="20" />')
            add(f'      <text x="21" y="{cy + 14}" font-size="9.5" fill="var(--ink-on-tape)">{esc(say(txt, lang))}</text>')
        else:
            add(f'      <text x="{cx + 47}" y="{cy + 14}" font-size="9.5" text-anchor="middle" fill="var(--ink-on-tape)">{esc(say(txt, lang))}</text>')
    add("    </g>")
    add(f'    <text x="14" y="170" font-size="9.5" fill="var(--ink-muted)">{esc(say(node["note"], lang))}</text>')
    add(f'    <text x="14" y="186" font-size="9.5" class="code-text" fill="var(--ink-muted)">{esc(say(node["cost"], lang))}</text>')
    add("  </g>")


CARD_RENDERERS = {
    "chip": draw_chip,
    "cloudflare": draw_cloudflare,
    "gateway": draw_gateway,
    "colab": draw_colab,
}


def draw_grid_cell(add, box: Box, cell: dict, lang: str, style: str) -> None:
    """One cell of a grid section: a card with a title, a code line and a note."""
    if style == "prop":
        add(f'    <g transform="translate({box.x:.1f},{box.y:.1f})">')
        add(f'      <rect x="0" y="0" width="{box.w:.0f}" height="{box.h:.0f}" rx="10" fill="var(--bg-raise)" />')
        add(f'      <rect x="12" y="14" width="6" height="6" rx="3" fill="{ACCENTS[cell["accent"]]}" />')
        add(f'      <text x="28" y="20" font-size="12.5" font-weight="650" fill="var(--ink-primary)">{esc(say(cell["title"], lang))}</text>')
        for k, line in enumerate(wrap(say(cell["body"], lang), box.w - 24, 11)):
            add(f'      <text x="12" y="{42 + k * 16}" font-size="11" fill="var(--ink-secondary)">{esc(line)}</text>')
        add(f'      <text x="12" y="{box.h - 14}" font-size="10.5" class="code-text" fill="var(--ink-muted)">{esc(say(cell["code"], lang))}</text>')
        add("    </g>")
    else:
        add(f'  <g transform="translate({box.x:.1f},{box.y:.1f})">')
        add(f'    <rect x="0" y="0" width="{box.w:.0f}" height="{box.h:.0f}" rx="11" fill="var(--bg-card)" stroke="var(--border-card)" stroke-width="1.4" />')
        add(f'    <rect x="0" y="0" width="{box.w:.0f}" height="4" rx="2" fill="{ACCENTS[cell["accent"]]}" />')
        add(f'    <text x="14" y="26" font-size="13" font-weight="650" fill="var(--ink-primary)">{esc(say(cell["title"], lang))}</text>')
        add(f'    <text x="14" y="45" font-size="11.5" class="code-text" fill="var(--ink-secondary)">{esc(say(cell["code"], lang))}</text>')
        add(f'    <text x="14" y="62" font-size="10.5" fill="var(--ink-muted)">{esc(say(cell["note"], lang))}</text>')
        add("  </g>")


def draw_section_tape(add, x: float, y: float, title: str, note: str | None,
                      colour: str) -> float:
    """A section heading: a coloured tape with the title, plus a note beside it."""
    w = text_width(title, 12.5) + 26
    add(f"""  <g transform="translate({x:.0f},{y:.0f})">
    <rect x="0" y="0" width="{w:.0f}" height="24" rx="4" fill="{colour}" opacity="0.9" />""")
    add(f'    <text x="13" y="17" font-size="12.5" font-weight="700" fill="var(--ink-on-tape)">{esc(title)}</text>')
    if note:
        add(f'    <text x="180" y="17" font-size="12.5" fill="var(--ink-muted)">{esc(note)}</text>')
    add("  </g>")
    return w


def build(spec: dict, lang: str) -> tuple[str, float]:
    """Render the diagram described by the spec."""
    o: list[str] = []
    add = o.append
    flows: list[tuple[str, str]] = []   # (path id, animation class)

    canvas = spec["canvas"]
    margin = canvas["margin"]
    content_w = canvas["width"] - 2 * margin
    sections = {s["id"]: s for s in spec["sections"]}

    # ── layout: one Graphviz run per section ──────────────────────────────
    request = sections["request"]
    node_sizes = {n["id"]: (n["w"], n["h"]) for n in request["nodes"]}
    req = dot(dot_row(
        [n["id"] for n in request["nodes"]], node_sizes, span=content_w,
        edges=[(e["from"], e["to"]) for e in request["edges"]],
    ))

    states = sections["states"]
    # 内容离外框的留白，由 spec 声明，不在代码里写死
    state_pad = states.get("padding", 0)
    props_pad = sections["props"].get("padding", 0)
    boot = states["boot"]
    boot_sizes = {k: tuple(v) for k, v in boot["sizes"].items()}
    boot_layout = dot(dot_row(boot["row"], boot_sizes, span=content_w - 2 * state_pad,
                              edges=[(a, b) for a, b, _ in boot["edges"]]))
    failure = states["failure"]
    fail_sizes = {k: tuple(v) for k, v in failure["sizes"].items()}

    grid_layouts = {}
    for s in spec["sections"]:
        if s["kind"] != "grid":
            continue
        cells = s["cells"]
        per_row = s["columns"]
        ids = [f"c{i}" for i in range(len(cells))]
        sizes = {f"c{i}": (c["w"], c["h"]) for i, c in enumerate(cells)}
        layout = dot(dot_row(ids, sizes, span=content_w - 2 * s.get("padding", 0), invisible=True))
        grid_layouts[s["id"]] = (layout, cells, per_row)

    # ── vertical composition ──────────────────────────────────────────────
    req_y = 310
    sec2_y = req_y + req.height + 62
    rail_y = sec2_y + 42
    states_y = rail_y + 30
    fails_y = states_y + 36 + 46
    trunk_y = states_y + 36 + 20
    ret_y = fails_y + 34 + 18
    bill_y = ret_y + 26
    rail_h = bill_y + 22 + state_pad - rail_y
    sec3_y = bill_y + 22 + 18 + 40
    gates_y = sec3_y + 40
    gate_cell_h = grid_layouts["gates"][1][0]["h"]
    prop_cell_h = grid_layouts["props"][1][0]["h"]
    props_frame_y = gates_y + gate_cell_h + 24
    props_y = props_frame_y + 44
    legend_y = props_y + prop_cell_h + 42
    canvas_h = legend_y + 24

    # ── canvas ────────────────────────────────────────────────────────────
    add(f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'viewBox="0 0 {canvas["width"]} {canvas_h:.0f}" width="{canvas["width"]}" height="{canvas_h:.0f}" '
        f'preserveAspectRatio="xMidYMid meet" role="img" aria-label="{esc(say(spec["title_attr"], lang))}">')
    add("  <title>" + esc(say(spec["title_attr"], lang)) + "</title>")
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
      @keyframes pulse { 0%,100% { opacity: .35; } 50% { opacity: .9; } }
      @keyframes breathe { 0%,100% { opacity: .85; } 50% { opacity: 1; } }
      .pulse { animation: pulse 2.6s ease-in-out infinite; }
      .breathe { animation: breathe 3.4s ease-in-out infinite; }
      @media (prefers-reduced-motion: reduce) {
        #movers { display: none; }
      }
    </style>""")
    add("""    <pattern id="dot-grid" width="22" height="22" patternUnits="userSpaceOnUse">
      <circle cx="2" cy="2" r="1.2" fill="var(--grid-color)" />
    </pattern>""")
    add("    " + arrow_marker("arrow-amber", "var(--amber)"))
    add("    " + arrow_marker("arrow-plum", "var(--ink-muted)"))
    add(f"""    <linearGradient id="g-amber" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{AMBER}" /><stop offset="1" stop-color="{ORANGE}" />
    </linearGradient>""")
    add(f"""    <linearGradient id="g-lime" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="{LIME}" /><stop offset="1" stop-color="{AMBER}" />
    </linearGradient>""")
    add(f"""    <linearGradient id="g-hero" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#251d2f" /><stop offset="1" stop-color="#1b1523" />
    </linearGradient>""")
    add(f"""    <linearGradient id="g-beam" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="{AMBER}" /><stop offset="0.55" stop-color="{LIME}" />
      <stop offset="1" stop-color="{AMBER}" />
    </linearGradient>""")
    add("""    <radialGradient id="g-hero-glow" cx="0.5" cy="0.5" r="0.5">
      <stop offset="0" stop-color="#f2ff59" stop-opacity="0.20" />
      <stop offset="1" stop-color="#f2ff59" stop-opacity="0" />
    </radialGradient>""")
    for name, path in LOGO_PATHS.items():
        fill, viewbox, _size = LOGO_STYLE[name]
        add(f'    <symbol id="logo-{name}" viewBox="{viewbox}"><path fill="{fill}" d="{path}" /></symbol>')
    # the project's own mark, inlined so the SVG stays self-contained. Its ids are
    # renamed on the way in: the source reuses short ids that would collide here.
    mark = (ROOT / "assets" / "logo" / "logo-e-fusion-dark.svg").read_text(encoding="utf-8")
    inner = mark.split(">", 1)[1].rsplit("</svg>", 1)[0]
    for src, dst in (("t", "mark-t"), ("L", "mark-L"), ("y", "mark-y"), ("o", "mark-o")):
        inner = inner.replace(f'id="{src}"', f'id="{dst}"').replace(f"url(#{src})", f"url(#{dst})")
    add(f'    <symbol id="kit-mark" viewBox="0 0 256 256">{inner.strip()}</symbol>')
    # The bare glyph, cropped to its own ink. The tile is plum, so on the plum
    # gate header it would vanish and leave the E floating off-centre.
    glyph = inner.replace('<rect width="256" height="256" fill="#211927"/>', "")
    glyph = glyph.replace('<rect width="256" height="256" fill="#211927" />', "")
    add(f'    <symbol id="kit-glyph" viewBox="58 41 166 174">{glyph.strip()}</symbol>')
    add("  </defs>")

    add(f'  <rect width="{canvas["width"]}" height="{canvas_h:.0f}" fill="var(--bg-canvas)" />')
    add(f'  <rect width="{canvas["width"]}" height="{canvas_h:.0f}" fill="url(#dot-grid)" />')

    # ── header ──
    add(f"""  <g transform="translate({margin},34)">
    <rect x="0" y="0" width="6" height="46" rx="3" fill="url(#g-amber)" />""")
    add('    <use href="#kit-mark" x="20" y="-1" width="28" height="28" />')
    add('    <text x="58" y="22" font-size="23" font-weight="700" fill="var(--ink-primary)">Comfy Colab Kit</text>')
    add(f'    <text x="58" y="44" font-size="14" fill="var(--ink-secondary)">{esc(say(spec["header"]["subtitle"], lang))}</text>')
    add("  </g>")
    badge = say(spec["header"]["badge"], lang)
    badge_w = text_width(badge, 12.5) + 34
    add(f"""  <g transform="translate({margin},100)">
    <rect x="0" y="0" width="{badge_w:.0f}" height="30" rx="15" fill="var(--bg-raise)" stroke="var(--border-card)" stroke-width="1.2" />
    <circle cx="17" cy="15" r="5" fill="var(--mint)" class="pulse" />""")
    add(f'    <text x="30" y="20" font-size="12.5" fill="var(--ink-secondary)">{esc(badge)}</text>')
    add("  </g>")
    add(f'  <text x="{margin}" y="162" font-size="12.5" fill="var(--ink-muted)">{esc(say(spec["header"]["headline"], lang))}</text>')

    hero_panel(add)

    # ── ① request path ──
    draw_section_tape(add, margin, req_y - 40, say(request["title"], lang),
                      say(request["note"], lang), "var(--yellow)")
    req = req.shift_to(margin).offset(0, req_y)
    for node in request["nodes"]:
        CARD_RENDERERS[node["card"]](add, req.box(node["id"]), node, lang)

    add("  <!-- request path edges -->")
    for n, edge in enumerate(request["edges"]):
        pid = f"flow-req-{n}"
        d = extend_to_border(req.edge(edge["from"], edge["to"]), req.box(edge["to"]))
        add(f'  <path id="{pid}" d="{d}" {edge_attrs("arrow-amber", stroke="var(--amber)", width=2.2)} />')
        flows.append((pid, ("mover", "mover-b", "mover-c", "mover-d")[n % 4]))
        if edge.get("label"):
            mx, my = req.edge_midpoint(edge["from"], edge["to"])
            add(f'  <text x="{mx:.1f}" y="{my - 8:.1f}" font-size="10" class="code-text" '
                f'fill="var(--ink-muted)" text-anchor="middle">{esc(say(edge["label"], lang))}</text>')

    # ── ② state machine ──
    draw_section_tape(add, margin, sec2_y, say(states["title"], lang),
                      say(states["note"], lang), "var(--green)")
    add(f"""  <g>
    <rect x="{margin}" y="{rail_y}" width="{content_w}" height="{rail_h:.0f}" rx="14" fill="var(--bg-card)" stroke="var(--border-card)" stroke-width="1.3" />""")

    boot_layout = boot_layout.shift_to(margin + state_pad).offset(0, states_y)
    # the failure row starts under `booting` and has to end at the inner edge
    # of the frame, so its width follows from where `booting` ended up
    fail_layout = dot(dot_row(
        failure["row"], fail_sizes,
        span=content_w - 2 * state_pad - (boot_layout.box("booting").x - (margin + state_pad)),
        edges=[(a, b) for a, b, _ in failure["edges"]],
    ))
    fail_layout = fail_layout.shift_to(boot_layout.box("booting").x).offset(0, fails_y)

    add('    <g font-size="11.5" text-anchor="middle">')
    for name in boot["row"]:
        box = boot_layout.box(name)
        fill, ink, outlined = STATE_COLORS[STATE_KIND[name]]
        extra = f' stroke="var(--ink-muted)" stroke-width="1.5"' if outlined else ""
        cls = ' class="breathe"' if name == "ready" else ""
        add(f'      <g transform="translate({box.x:.1f},{box.y:.1f})"{cls}>')
        add(f'        <rect x="0" y="0" width="{box.w:.0f}" height="{box.h:.0f}" rx="9" fill="{fill}"{extra} />')
        add(f'        <text x="{box.w / 2:.0f}" y="{box.h / 2 + 5:.0f}" font-weight="650" fill="{ink}">{esc(name)}</text>')
        add("      </g>")
    for name in failure["row"]:
        box = fail_layout.box(name)
        fill, ink, _ = STATE_COLORS[STATE_KIND[name]]
        add(f'      <g transform="translate({box.x:.1f},{box.y:.1f})">')
        add(f'        <rect x="0" y="0" width="{box.w:.0f}" height="{box.h:.0f}" rx="9" fill="{fill}" />')
        add(f'        <text x="{box.w / 2:.0f}" y="{box.h / 2 + 5:.0f}" font-weight="650" fill="{ink}">{esc(name)}</text>')
        add("      </g>")
    add("    </g>")

    add("    <!-- boot chain edges -->")
    for n, (a, b, cap) in enumerate(boot["edges"]):
        pid = f"flow-boot-{n}"
        d = extend_to_border(boot_layout.edge(a, b), boot_layout.box(b))
        add(f'    <path id="{pid}" d="{d}" {edge_attrs("arrow-amber", stroke="var(--amber)", width=2.2)} />')
        flows.append((pid, "mover" if n % 2 == 0 else "mover-d"))
        mx, my = boot_layout.edge_midpoint(a, b)
        add(f'    <text x="{mx:.1f}" y="{states_y - 9:.1f}" font-size="9.5" class="code-text" '
            f'fill="var(--ink-muted)" text-anchor="middle">{esc(say(cap, lang))}</text>')

    # failure bus: every boot state drops onto one trunk, one arrow into `failed`
    failed_box = fail_layout.box("failed")
    for name in failure["bus_from"]:
        b = boot_layout.box(name)
        x = b.x + b.w / 2
        add(f'    <path d="M{x:.1f} {b.bottom:.1f} V{trunk_y:.1f} H{failed_box.x + failed_box.w / 2:.1f} '
            f'V{failed_box.y:.1f}" {edge_attrs("arrow-plum", width=1.5, dash="4 4")} />')
    add(f'    <text x="{margin + 62}" y="{trunk_y + 16:.1f}" font-size="9.5" class="code-text" '
        f'fill="var(--ink-muted)">{esc(say(states["notes"]["fail"], lang))}</text>')

    for a, b, cap in failure["edges"]:
        d = extend_to_border(fail_layout.edge(a, b), fail_layout.box(b))
        add(f'    <path d="{d}" {edge_attrs("arrow-plum", width=1.6)} />')
    stopping = fail_layout.box("stopping")
    add(f'    <text x="{(failed_box.right + stopping.x) / 2:.1f}" y="{fails_y - 8:.1f}" '
        f'font-size="9.5" class="code-text" fill="var(--ink-muted)" text-anchor="middle">stop</text>')

    # the return line: stop is allowed from any non-sleeping state
    sleeping = boot_layout.box("sleeping")
    ready = boot_layout.box("ready")
    switching = fail_layout.box("switching")
    add(f'    <path d="M{stopping.cx:.1f} {stopping.bottom:.1f} V{ret_y:.1f} '
        f'H{sleeping.cx:.1f} V{sleeping.bottom:.1f}" {edge_attrs("arrow-plum", width=1.6)} />')
    add(f'    <text x="{margin + 200}" y="{ret_y - 7:.1f}" font-size="9.5" class="code-text" '
        f'fill="var(--ink-muted)">{esc(say(states["notes"]["stop"], lang))}</text>')
    # the drop has to turn *above* the failure row, otherwise the last leg
    # runs upwards and the head points into the box instead of onto it
    add(f'    <path d="M{ready.cx:.1f} {ready.bottom:.1f} V{fails_y - 20:.1f} '
        f'H{switching.cx:.1f} V{switching.top:.1f}" {edge_attrs("arrow-plum", width=1.5, dash="4 4")} />')
    add(f'    <text x="{margin + content_w - 8}" y="{switching.bottom + 15:.1f}" font-size="9.5" '
        f'class="code-text" fill="var(--ink-muted)" text-anchor="end">{esc(say(states["notes"]["switch"], lang))}</text>')

    add(f"""    <g transform="translate({margin + state_pad},{bill_y})">
      <rect x="0" y="0" width="{content_w - 2 * state_pad}" height="22" rx="6" fill="var(--rose)" opacity="0.2" />
      <text x="{(content_w - 2 * state_pad) / 2:.0f}" y="15" font-size="10.5" text-anchor="middle" fill="var(--ink-secondary)">{esc(say(states["notes"]["billing"], lang))}</text>
    </g>""")
    add("  </g>")

    # ── ③ the grid sections ──
    for sid, colour in (("gates", "var(--rose)"), ("props", "var(--green)")):
        section = sections[sid]
        layout, cells, _cols = grid_layouts[sid]
        pad = section.get("padding", 0)
        if sid == "gates":
            draw_section_tape(add, margin, sec3_y, say(section["title"], lang),
                              say(section["note"], lang), colour)
            y = gates_y
        else:
            # the properties live in a frame, so the heading goes inside it
            y = props_y
            add(f"""  <g transform="translate({margin},{props_frame_y})">
    <rect x="0" y="0" width="{content_w}" height="{44 + prop_cell_h + props_pad}" rx="14" fill="var(--bg-card)" stroke="var(--border-card)" stroke-width="1.3" />
    <text x="{pad}" y="26" font-size="13" font-weight="700" fill="var(--ink-primary)">{esc(say(section["title"], lang))}</text>""")
        layout = layout.shift_to(margin + pad).offset(0, y)
        style = "prop" if sid == "props" else "gate"
        for i, cell in enumerate(cells):
            box = layout.box(f"c{i}")
            if style == "prop":
                # the property cards sit inside the frame, so they are drawn
                # relative to the frame's own origin
                box = Box(box.x - margin, box.y - props_frame_y, box.w, box.h)
            draw_grid_cell(add, box, cell, lang, style)
        if sid == "props":
            add("  </g>")

    # ── legend ──
    add(f'  <g transform="translate({margin},{legend_y})" font-size="10.5">')
    names = spec["legend"][lang]
    dots = [("var(--mint)", names[0]), ("var(--yellow)", names[1]), ("var(--rose)", names[2]),
            ("var(--blue)", names[3]), ("var(--violet)", names[4])]
    x = 0.0
    for col, name in dots:
        add(f'    <circle cx="{x + 5:.1f}" cy="-4" r="4" fill="{col}" />')
        add(f'    <text x="{x + 16:.1f}" y="0" fill="var(--ink-muted)">{esc(name)}</text>')
        x += 16 + text_width(name, 10.5) + 18
    # a sample of the dashed line, on the same optical line as the labels
    lx = x + 20
    add(f'    <path d="M{lx:.1f} -4 H{lx + 28:.1f}" stroke="var(--ink-muted)" stroke-width="1.5" stroke-dasharray="4 4" fill="none" />')
    add(f'    <text x="{lx + 36:.1f}" y="0" fill="var(--ink-muted)">dashed = failure path</text>')
    add(f'    <text x="{content_w}" y="0" class="code-text" fill="var(--ink-muted)" text-anchor="end">comfy-colab-kit</text>')
    add("  </g>")

    # ── movers: each light is bound to the path it travels along ──
    add("  <!-- flow movers: bound to the path they travel, never off the line -->")
    add('  <g id="movers">')
    for n, (pid, cls) in enumerate(flows):
        r = 3.0 if n % 3 else 2.6
        halo = r * 2.1
        add(f'    <g class="{cls}">')
        add(f'      <animateMotion dur="{MOVER_DUR[n % len(MOVER_DUR)]}" '
            f'begin="{-0.7 * (n % 4):.1f}s" repeatCount="indefinite" rotate="auto">')
        add(f'        <mpath href="#{pid}" xlink:href="#{pid}" />')
        add("      </animateMotion>")
        add(f'      <circle r="{halo:.1f}" fill="var(--amber)" opacity="0.2"/>')
        add(f'      <circle r="{r:.1f}" fill="var(--amber)"/>')
        add("    </g>")
    add("  </g>")

    add("</svg>")
    return "\n".join(o) + "\n", canvas_h


def main() -> int:
    spec = load_spec()
    for lang, name in (("en", "flow.svg"), ("zh", "flow.zh.svg")):
        svg, height = build(spec, lang)
        (OUT_DIR / name).write_text(svg, encoding="utf-8")
        print(f"wrote assets/architecture/{name}  ({len(svg) / 1024:.0f} KB, {height:.0f} px tall)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
