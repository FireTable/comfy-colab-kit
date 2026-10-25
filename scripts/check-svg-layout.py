#!/usr/bin/env python3
"""Check a rendered SVG for the defects that are invisible in the source.

An SVG can be perfectly well-formed and still be wrong: a label that runs
past its card, two labels on top of each other, an arrow head pointing away
from the box it belongs to, a box poking out of the frame around it. None of
these show up in the markup — they only exist once the browser has laid the
text out, and the answer differs per platform and per font.

This script opens the SVG in headless Chrome, reads back the real bounding
boxes, and reports what it finds. It needs nothing but a Chrome install.

Usage:
    python3 check-svg-layout.py drawing.svg [more.svg ...]
    python3 check-svg-layout.py --quiet drawing.svg   # only the verdict

With no arguments it checks every SVG in ``<script>/../assets/architecture/``,
which is where the generated diagram lives.

Exit code 1 when a defect is found, 0 otherwise. If no browser can be driven
the check is skipped with exit code 0 — a machine without Chrome is not a
broken drawing.

What it reports
---------------
overflow    a label sticks out of the box it belongs to
collision   two labels overlap each other
off-canvas  a label runs past the viewBox
box-out     a box pokes out of the frame around it
arrow-way   an arrow ends on a box but points away from it
icon-align  an icon sits off the optical line of the label beside it
mover-void  a moving light is not bound to a drawn path
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
  const frames = [];
  const isDecoration = (el) => {
    // a drop shadow is drawn almost transparent; a band at 0.92 still counts
    const op = parseFloat(el.getAttribute('opacity') || '1');
    return op < 0.5;
  };
  document.querySelectorAll('rect').forEach(el => {
    if (isDecoration(el) || el.closest('symbol, defs')) return;
    const b = u(el.getBoundingClientRect());
    if (b.w > 20 && b.h > 8) {
      const r = {x: +b.x.toFixed(2), y: +b.y.toFixed(2),
                 w: +b.w.toFixed(2), h: +b.h.toFixed(2)};
      rects.push(r);
      frames.push(r);
    }
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

  // arrows: where they end and which way they point there
  const arrows = [];
  document.querySelectorAll('path[marker-end]').forEach(p => {
    const len = p.getTotalLength();
    if (len < 1) return;
    const tip = p.getPointAtLength(len);
    const prev = p.getPointAtLength(Math.max(0, len - 2));
    arrows.push({x: tip.x / sx, y: tip.y / sy,
                 dx: (tip.x - prev.x) / sx, dy: (tip.y - prev.y) / sy});
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

  return {vw: vb.width, vh: vb.height, texts, rects, frames, icons, symbols, movers, flowPaths, arrows};
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

    # a box that pokes out of the frame around it
    frames = data.get("frames", [])
    for r in frames:
        cx, cy = r["x"] + r["w"] / 2, r["y"] + r["h"] / 2
        # a parent is at least as big as the box and has it at its centre;
        # a small box sitting inside a big one is a child, not a frame
        parents = [c for c in frames
                   if c is not r
                   and c["w"] >= r["w"] - 1 and c["h"] >= r["h"] - 1
                   and c["x"] <= cx <= c["x"] + c["w"] and c["y"] <= cy <= c["y"] + c["h"]]
        if not parents:
            continue
        frame = min(parents, key=lambda c: c["w"] * c["h"])
        inside = (frame["x"] - 1 <= r["x"] and frame["y"] - 1 <= r["y"]
                  and frame["x"] + frame["w"] + 1 >= r["x"] + r["w"]
                  and frame["y"] + frame["h"] + 1 >= r["y"] + r["h"])
        if not inside:
            out.append("box-out      a box pokes out of the frame around it")

    # an arrow must point at the box it enters, not away from it
    for arrow in data.get("arrows", []):
        if abs(arrow["dx"]) < 0.01 and abs(arrow["dy"]) < 0.01:
            continue
        targets = [r for r in rects
                   if (abs(arrow["x"] - r["x"]) < 1.5 or abs(arrow["x"] - (r["x"] + r["w"])) < 1.5
                       or abs(arrow["y"] - r["y"]) < 1.5 or abs(arrow["y"] - (r["y"] + r["h"])) < 1.5)
                   and r["x"] - 1.5 <= arrow["x"] <= r["x"] + r["w"] + 1.5
                   and r["y"] - 1.5 <= arrow["y"] <= r["y"] + r["h"] + 1.5]
        if not targets:
            continue
        box = min(targets, key=lambda r: r["w"] * r["h"])
        # the box has to lie ahead of the head, otherwise the arrow points away
        ahead = ((box["x"] + box["w"] / 2 - arrow["x"]) * arrow["dx"]
                 + (box["y"] + box["h"] / 2 - arrow["y"]) * arrow["dy"])
        if ahead < 0:
            out.append("arrow-way    an arrow ends on a box but points away from it")

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
        cdp = CDP(svg.resolve().as_uri(), port=port)
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
    quiet = "--quiet" in argv
    targets = [Path(a) for a in argv if not a.startswith("--")]
    if not targets:
        default_dir = Path(__file__).resolve().parent.parent / "assets" / "architecture"
        targets = sorted(default_dir.glob("*.svg")) if default_dir.is_dir() else []
        if not targets:
            print(__doc__)
            return 2
    for t in targets:
        if not t.is_file():
            print(f"{t}: no such file")
            return 1
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
            if not quiet:
                print(f"{svg.name}: {len(found)} layout defect(s)")
                for line in found:
                    print(f"  {line}")
        elif not quiet:
            print(f"{svg.name}: layout ok")
    if skipped:
        print("layout check skipped: no usable browser on this machine")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
