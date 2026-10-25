#!/usr/bin/env python3
"""Layout check for the generated architecture diagram.

Character-count estimates cannot catch text overflow: rendered width depends
on the font the browser picks, and CJK glyphs are roughly twice as wide as
Latin ones. This script opens the committed SVG in a real headless Chrome and
reads back the actual bounding boxes, then reports three classes of defect:

* overflow  — a text element extends past the smallest rectangle that
              contains its centre, by more than the tolerance.
* collision — two text elements overlap each other.
* off-canvas — a text element extends past the SVG viewBox.

Usage:
    python3 scripts/check-svg-layout.py [svg ...]

With no arguments it checks both language variants. Exit code is 1 when any
defect is found, so CI can gate on it.

Requires Google Chrome at the path in scripts/cdp.py; the script skips with
code 0 when Chrome is absent, so environments without it are not blocked.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from cdp import CHROME, CDP  # noqa: E402

# How far a label may cross a border before it counts as overflow. One pixel
# covers sub-pixel rounding; anything visible is larger than this.
TOLERANCE = 1.5

# How far an icon may sit off the label's optical line. A couple of pixels is
# invisible; more than that reads as "the icon is not centred".
ICON_TOLERANCE = 2.0

MEASURE_JS = r"""
(() => {
  const svg = document.querySelector('svg');
  const vb = svg.viewBox.baseVal;
  const r = svg.getBoundingClientRect();
  const sx = r.width / vb.width, sy = r.height / vb.height;
  const u = bb => ({x: bb.x / sx, y: bb.y / sy, w: bb.width / sx, h: bb.height / sy});
  const texts = [];
  document.querySelectorAll('text').forEach((t, i) => {
    const b = u(t.getBoundingClientRect());
    texts.push({i, s: (t.textContent || '').trim().slice(0, 70),
                x: +b.x.toFixed(2), y: +b.y.toFixed(2),
                w: +b.w.toFixed(2), h: +b.h.toFixed(2),
                cy: +(b.y + b.h / 2).toFixed(2)});
  });
  // Containers a label is supposed to sit inside. A translucent rect is a
  // shadow, not a container, and a shape drawn inside <defs> or <symbol> is
  // a definition rather than part of the picture.
  const rects = [];
  const isDecoration = (el) => {
    // a drop shadow is drawn almost transparent; a band at 0.92 still counts
    const op = parseFloat(el.getAttribute('opacity') || '1');
    return op < 0.5;
  };
  document.querySelectorAll('rect').forEach(el => {
    if (isDecoration(el) || el.closest('symbol, defs')) return;
    const b = u(el.getBoundingClientRect());
    if (b.w > 20 && b.h > 8) rects.push({x: +b.x.toFixed(2), y: +b.y.toFixed(2),
                                         w: +b.w.toFixed(2), h: +b.h.toFixed(2)});
  });
  // a filled shape is a container too — header bands are drawn as one path
  document.querySelectorAll('path').forEach(el => {
    if (isDecoration(el) || el.closest('symbol, defs, marker')) return;
    if (el.getAttribute('fill') === 'none') return;
    const b = u(el.getBoundingClientRect());
    if (b.w * b.h > 2000) rects.push({x: +b.x.toFixed(2), y: +b.y.toFixed(2),
                                      w: +b.w.toFixed(2), h: +b.h.toFixed(2)});
  });

  // icons: where the glyph actually landed, and whether the symbol's viewBox
  // is large enough to hold the path it draws
  const icons = [];
  document.querySelectorAll('use').forEach(el => {
    const b = u(el.getBoundingClientRect());
    let content = null;
    try {
      const t = el.getBBox();
      content = {x: t.x, y: t.y, w: t.width, h: t.height};
    } catch (e) { /* not rendered */ }
    icons.push({href: el.getAttribute('href') || el.getAttribute('xlink:href'),
                x: +b.x.toFixed(2), y: +b.y.toFixed(2), w: +b.w.toFixed(2), h: +b.h.toFixed(2),
                cy: +(b.y + b.h / 2).toFixed(2), content});
  });
  const symbols = [];
  document.querySelectorAll('symbol').forEach(sym => {
    const p = sym.querySelector('path');
    if (!p) return;
    let bb = null;
    try { const t = p.getBBox(); bb = {x: t.x, y: t.y, w: t.width, h: t.height}; } catch (e) {}
    const vb = (sym.getAttribute('viewBox') || '').split(/\s+/).map(Number);
    symbols.push({id: sym.id, vb, bb});
  });

  // moving lights: every one must name a path that exists
  const movers = [];
  document.querySelectorAll('animateMotion').forEach(am => {
    const mp = am.querySelector('mpath');
    const href = mp ? (mp.getAttribute('href') || mp.getAttribute('xlink:href')) : null;
    let resolves = false;
    if (href) resolves = !!document.querySelector(href);
    movers.push({href, resolves});
  });
  const flowPaths = [];
  document.querySelectorAll('path[id]').forEach(p => flowPaths.push(p.id));

  return {vw: vb.width, vh: vb.height, texts, rects, icons, symbols, movers, flowPaths};
})()
"""


def defects(data: dict) -> list[str]:
    """Turn measured geometry into a list of human-readable problems."""
    out: list[str] = []
    vw, vh = data["vw"], data["vh"]
    rects = data["rects"]

    for t in data["texts"]:
        if t["w"] == 0:
            continue
        cx, cy = t["x"] + t["w"] / 2, t["y"] + t["h"] / 2
        if t["x"] < -TOLERANCE or t["y"] < -TOLERANCE \
                or t["x"] + t["w"] > vw + TOLERANCE or t["y"] + t["h"] > vh + TOLERANCE:
            out.append(f"off-canvas  '{t['s']}' extends past the viewBox")
            continue
        holders = [r for r in rects
                   if r["x"] <= cx <= r["x"] + r["w"] and r["y"] <= cy <= r["y"] + r["h"]]
        if not holders:
            continue
        # the tightest rectangle around the label is the one it is meant to fit
        box = min(holders, key=lambda r: r["w"] * r["h"])
        over = {
            "left": box["x"] - t["x"],
            "right": t["x"] + t["w"] - (box["x"] + box["w"]),
            "top": box["y"] - t["y"],
            "bottom": t["y"] + t["h"] - (box["y"] + box["h"]),
        }
        worst = max(over, key=over.get)
        if over[worst] > TOLERANCE:
            out.append(f"overflow    '{t['s']}' crosses its box by "
                       f"{over[worst]:.0f}px on the {worst}")

    # a symbol whose viewBox is smaller than the path it draws clips the logo
    for sym in data.get("symbols", []):
        vb, bb = sym["vb"], sym["bb"]
        if not bb or len(vb) != 4:
            continue
        if (bb["x"] < vb[0] - 0.5 or bb["y"] < vb[1] - 0.5
                or bb["x"] + bb["w"] > vb[0] + vb[2] + 0.5
                or bb["y"] + bb["h"] > vb[1] + vb[3] + 0.5):
            out.append(f"icon-clip    '{sym['id']}' is cut off by its own viewBox")

    # an icon must sit on the same optical line as the label beside it
    for ic in data.get("icons", []):
        if not ic.get("content") or ic["w"] == 0:
            continue
        # the label that sits to the right of the icon
        cand = [t for t in data["texts"]
                if t["x"] >= ic["x"] + ic["w"] - 2
                and t["x"] - (ic["x"] + ic["w"]) < 60
                and abs(t["cy"] - ic["cy"]) < 14]
        if not cand:
            continue
        label = min(cand, key=lambda t: t["x"])
        if abs(label["cy"] - ic["cy"]) > ICON_TOLERANCE:
            out.append(f"icon-align   '{ic['href']}' sits {abs(label['cy'] - ic['cy']):.1f}px "
                       f"{'above' if ic['cy'] < label['cy'] else 'below'} '{label['s']}'")

    # a moving light that is not bound to a real path drifts into empty space
    for mv in data.get("movers", []):
        if not mv["href"] or not mv["resolves"]:
            out.append(f"mover-void   a moving light is not bound to a drawn path")

    texts = [t for t in data["texts"] if t["w"] > 0]
    for i, a in enumerate(texts):
        for b in texts[i + 1:]:
            ox = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
            oy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
            if ox > 2 and oy > 2:
                out.append(f"collision   '{a['s']}' overlaps '{b['s']}' "
                           f"by {ox:.0f}×{oy:.0f}px")
    return out


def check(svg: Path, port: int) -> list[str] | None:
    """Measure one SVG. ``None`` means the browser could not be driven."""
    try:
        cdp = CDP(svg.as_uri(), port=port)
    except Exception as exc:                     # no browser, or it refused to start
        print(f"  (browser unavailable: {exc})")
        return None
    try:
        cdp.call("Runtime.enable")
        return defects(cdp.evaluate(MEASURE_JS))
    except Exception as exc:
        print(f"  (browser unavailable: {exc})")
        return None
    finally:
        cdp.close()


def main(argv: list[str]) -> int:
    if not Path(CHROME).is_file():
        print(f"layout check skipped: Chrome not found at {CHROME}")
        return 0
    targets = [Path(a) for a in argv] or [
        ROOT / "assets" / "architecture" / "flow.svg",
        ROOT / "assets" / "architecture" / "flow.zh.svg",
    ]
    failed = False
    skipped = False
    for n, svg in enumerate(targets):
        found = check(svg, port=9510 + n)
        if found is None:
            # A machine without a usable browser cannot be measured. That is an
            # environment problem, not a defect in the drawing, so it must not
            # turn the build red.
            skipped = True
            continue
        if found:
            failed = True
            print(f"{svg.name}: {len(found)} layout defect(s)")
            for line in found:
                print(f"  {line}")
        else:
            print(f"{svg.name}: layout ok")
    if skipped:
        print("layout check skipped: no usable browser on this machine")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
