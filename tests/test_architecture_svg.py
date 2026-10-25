"""Guards for the generated architecture diagram.

The diagram is produced by `scripts/build-architecture-svg.py`, which derives every
edge from the geometry of the boxes it connects. These tests pin the invariants
that make the arrows land on their targets, and keep the English and Chinese
variants in sync.
"""
from __future__ import annotations

import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FLOW = ROOT / "assets" / "architecture" / "flow.svg"
FLOW_ZH = ROOT / "assets" / "architecture" / "flow.zh.svg"
GENERATOR = ROOT / "scripts" / "build-architecture-svg.py"

SVG_NS = "{http://www.w3.org/2000/svg}"


@pytest.fixture(scope="module")
def svgs() -> dict[str, str]:
    return {"en": FLOW.read_text(encoding="utf-8"), "zh": FLOW_ZH.read_text(encoding="utf-8")}


def _boxes_in_user_space(root) -> list[tuple[float, float, float, float]]:
    """Diagram boxes with their group transforms applied (absolute user space)."""
    boxes: list[tuple[float, float, float, float]] = []

    def walk(el, ox: float, oy: float):
        nonlocal boxes
        tx, ty = ox, oy
        t = el.get("transform") or ""
        m = re.match(r"translate\(\s*(-?[\d.]+)[ ,]+(-?[\d.]+)\s*\)", t)
        if m:
            tx += float(m.group(1))
            ty += float(m.group(2))
        if el.tag == f"{SVG_NS}rect":
            w, h = float(el.get("width", 0)), float(el.get("height", 0))
            # skip full-canvas backgrounds and tiny chips
            if 40 <= w <= 400 and 20 <= h <= 220:
                boxes.append((tx + float(el.get("x", 0)), ty + float(el.get("y", 0)), w, h))
        for child in el:
            walk(child, tx, ty)

    walk(root, 0.0, 0.0)
    return boxes


def _path_points(d: str) -> list[tuple[float, float]]:
    """Endpoints of an SVG path, following M/L/H/V/Q/C commands."""
    tokens = re.findall(r"[MLHVQCAZz]|-?\d+\.?\d*", d)
    pts: list[tuple[float, float]] = []
    i = 0
    cmd = ""
    cur = (0.0, 0.0)
    start = (0.0, 0.0)
    while i < len(tokens):
        t = tokens[i]
        if t.isalpha():
            cmd = t
            i += 1
            if cmd in ("M", "m") and i < len(tokens) and not tokens[i].isalpha():
                pass
            continue
        if cmd in ("M", "L", "T"):
            cur = (float(tokens[i]), float(tokens[i + 1]))
            i += 2
            if cmd == "M":
                start = cur
        elif cmd in ("m", "l", "t"):
            cur = (cur[0] + float(tokens[i]), cur[1] + float(tokens[i + 1]))
            i += 2
        elif cmd in ("H", "h"):
            dx = float(tokens[i])
            cur = (dx if cmd == "H" else cur[0] + dx, cur[1])
            i += 1
        elif cmd in ("V", "v"):
            dy = float(tokens[i])
            cur = (cur[0], dy if cmd == "V" else cur[1] + dy)
            i += 1
        elif cmd in ("Q", "C", "S", "A"):
            # skip the control points, but the last pair of C is the endpoint
            n = {"Q": 4, "C": 6, "S": 4, "A": 7}[cmd]
            vals = [float(v) for v in tokens[i:i + n]]
            if cmd == "C":
                cur = (vals[4], vals[5])
            elif cmd == "Q":
                cur = (vals[2], vals[3])
            elif cmd == "S":
                cur = (vals[2], vals[3])
            else:
                cur = (cur[0] + vals[5], cur[1] + vals[6])
            i += n
        else:
            i += 1
            continue
        pts.append(cur)
    return pts


def test_generator_is_deterministic(svgs):
    """Rebuilding must reproduce the committed files byte for byte."""
    r = subprocess.run([sys.executable, str(GENERATOR)], cwd=ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert FLOW.read_text(encoding="utf-8") == svgs["en"]
    assert FLOW_ZH.read_text(encoding="utf-8") == svgs["zh"]


@pytest.mark.parametrize("key", ["en", "zh"])
def test_svg_is_well_formed(svgs, key):
    root = ET.fromstring(svgs[key])
    assert root.tag == f"{SVG_NS}svg"
    vb = root.get("viewBox")
    assert vb.startswith("0 0 1180 "), vb
    # the canvas is sized by the layout, but it must match the intrinsic size
    h = vb.split()[3]
    assert root.get("width") == "1180" and root.get("height") == h


@pytest.mark.parametrize("key", ["en", "zh"])
def test_no_external_references(svgs, key):
    body = svgs[key]
    # the only allowed URL is the SVG namespace declaration
    urls = re.findall(r"https?://[^\s\"'<>]+", body)
    # the only allowed URLs are the SVG and xlink namespace declarations
    assert set(urls) <= {"http://www.w3.org/2000/svg",
                         "http://www.w3.org/1999/xlink"}, urls
    assert "@import" not in body
    # every image must be an inline data URI
    for href in re.findall(r'href="([^"]+)"', body):
        assert href.startswith("#") or href.startswith("data:image/"), href[:60]


@pytest.mark.parametrize("key", ["en", "zh"])
def test_no_html_entities(svgs, key):
    """SVG does not decode HTML entities, so &lt; would render literally."""
    body = svgs[key]
    # SVG does not decode HTML entities, so a literal &lt; would render as "&lt;"
    assert "&lt;" not in body
    assert "&gt;" not in body


@pytest.mark.parametrize("key", ["en", "zh"])
def test_arrow_markers_tip_on_endpoint(svgs, key):
    """The marker tip must sit on the path endpoint so heads touch their target."""
    root = ET.fromstring(svgs[key])
    markers = [e for e in root.iter(f"{SVG_NS}marker")]
    assert markers, "no arrow markers defined"
    for mk in markers:
        assert mk.get("markerUnits") == "userSpaceOnUse", mk.get("id")
        ref_x = float(mk.get("refX"))
        ref_y = float(mk.get("refY"))
        size = float(mk.get("markerWidth"))
        # the drawn triangle's extreme x must coincide with refX
        d = list(mk)[0].get("d")
        xs = [float(v) for i, v in enumerate(d.replace("M", " ").replace("L", " ")
                                            .replace("Z", " ").split()) if i % 2 == 0]
        assert max(xs) == pytest.approx(ref_x, abs=0.2), mk.get("id")
        assert ref_y == pytest.approx(size / 2, abs=0.2), mk.get("id")


@pytest.mark.parametrize("key", ["en", "zh"])
def test_every_arrow_edge_ends_on_a_box_border(svgs, key):
    """Each marked edge must terminate on the outline of one of the boxes."""
    root = ET.fromstring(svgs[key])
    boxes = _boxes_in_user_space(root)

    marked = 0
    for p in root.iter(f"{SVG_NS}path"):
        if p.get("marker-end") is None:
            continue
        marked += 1
        pts = _path_points(p.get("d", ""))
        assert pts, d
        ex, ey = pts[-1]
        on_border = any(
            (abs(ex - bx) < 0.6 or abs(ex - (bx + bw)) < 0.6) and by - 0.6 <= ey <= by + bh + 0.6
            or (abs(ey - by) < 0.6 or abs(ey - (by + bh)) < 0.6) and bx - 0.6 <= ex <= bx + bw + 0.6
            for bx, by, bw, bh in boxes
        )
        assert on_border, f"edge endpoint ({ex}, {ey}) is not on any box border"
    assert marked >= 12, f"expected the diagram to have many arrows, found {marked}"


def test_english_and_chinese_have_identical_structure(svgs):
    """Both language variants must be the same diagram, only the words differ."""
    def structure(body: str) -> list[str]:
        """Element skeleton, ignoring elements whose geometry tracks the wording.

        The badge pill and the section tapes are sized to their label, and the
        legend lays its entries out one after another, so those shift with the
        language. Everything else must be identical between the two variants.
        """
        root = ET.fromstring(body)
        out = []
        for el in root.iter():
            tag = el.tag.replace(SVG_NS, "")
            if tag in ("text", "title"):
                x = el.get("x")
                if tag == "text" and el.get("y") == "0":
                    x = "*"  # legend entry, laid out after the previous label
                out.append(f"{tag}:{x},{el.get('y')},{el.get('font-size')}")
            elif tag in ("rect", "circle", "path", "use", "image", "g"):
                w = el.get("width")
                x = el.get("x")
                if tag == "rect" and w and el.get("rx") in ("4", "15"):
                    # the badge pill and the section tapes: width follows the words
                    w, x = "*", "*"
                out.append(f"{tag}:{x},{el.get('y')},{w},{el.get('height')}")
        return out

    en, zh = structure(svgs["en"]), structure(svgs["zh"])
    assert en == zh, "the two language variants drifted apart"


def test_service_logos_are_present(svgs):
    """Cloudflare / Colab / ComfyUI / HuggingFace marks identify the services."""
    for key in ("en", "zh"):
        for name in ("cloudflare", "googlecolab", "comfyui", "huggingface"):
            assert f'id="logo-{name}"' in svgs[key], f"{name} logo missing in {key}"
        assert 'id="kit-mark"' in svgs[key], f"project mark missing in {key}"


def test_hero_image_is_inline(svgs):
    for key in ("en", "zh"):
        assert "data:image/webp;base64," in svgs[key]


def test_rendered_layout_has_no_overflow():
    """Open the committed SVGs in a real browser and check the text fits.

    Character counts cannot catch this: the width depends on the font the
    browser picks, and a CJK glyph is about twice a Latin one. The checker
    skips itself when Chrome is not installed, so CI without it still passes.
    """
    checker = ROOT / "scripts" / "check-svg-layout.py"
    r = subprocess.run([sys.executable, str(checker), str(FLOW), str(FLOW_ZH)],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
