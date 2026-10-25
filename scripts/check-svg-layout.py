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

# The least breathing room a box may leave inside the frame around it. Zero
# means the box is flush against the border, which reads as a mistake even
# when it was meant.
MIN_PADDING = 6.0

# A brand mark smaller than this is a smudge rather than a logo.
MIN_ICON_SIZE = 14.0

# How much of its box a brand mark has to fill to read as a mark.
MIN_ICON_FILL = 0.3

# Two items in the same group need at least this much room between them.
MIN_ITEM_GAP = 4.0

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

  // icons: where the glyph actually landed, what colour it is, and what is
  // behind it — a logo painted in the colour of its background is invisible
  const rootStyle = getComputedStyle(document.documentElement);
  const resolve = (spec) => {
    if (!spec) return null;
    const m = spec.match(/var\((--[\w-]+)\)/);
    return m ? rootStyle.getPropertyValue(m[1]).trim() : spec.trim();
  };
  const rgbOf = (spec) => {
    if (!spec) return null;
    let m = spec.match(/rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)/);
    if (m) return [+m[1], +m[2], +m[3]];
    m = spec.match(/^#([0-9a-f]{6})$/i);
    if (m) return [parseInt(m[1].slice(0, 2), 16), parseInt(m[1].slice(2, 4), 16), parseInt(m[1].slice(4, 6), 16)];
    m = spec.match(/^#([0-9a-f]{3})$/i);
    if (m) return [parseInt(m[1][0] + m[1][0], 16), parseInt(m[1][1] + m[1][1], 16), parseInt(m[1][2] + m[1][2], 16)];
    return null;
  };
  const painted = [];
  document.querySelectorAll('rect, path').forEach(el => {
    if (el.closest('symbol, defs, marker')) return;
    if (parseFloat(el.getAttribute('opacity') || '1') < 0.5) return;
    if (el.tagName.toLowerCase() === 'path' && el.getAttribute('fill') === 'none') return;
    const b = u(el.getBoundingClientRect());
    if (b.w < 2 || b.h < 2) return;
    painted.push({el, b});
  });

  const icons = [];
  document.querySelectorAll('use').forEach(el => {
    const b = u(el.getBoundingClientRect());
    let content = null;
    try {
      const t = el.getBBox();
      content = {x: t.x, y: t.y, w: t.width, h: t.height};
    } catch (e) { /* not rendered */ }
    const href = el.getAttribute('href') || el.getAttribute('xlink:href') || '';
    const sym = href.startsWith('#') ? document.querySelector(href) : null;
    const path = sym && sym.querySelector('path');
    const fill = path ? resolve(path.getAttribute('fill')) : null;
    // the shape painted right behind the icon
    const cx = b.x + b.w / 2, cy = b.y + b.h / 2;
    let behind = null;
    for (const p of painted) {
      if (p.el.contains(el) || p.el === el) continue;
      const pb = p.b;
      if (cx >= pb.x && cx <= pb.x + pb.w && cy >= pb.y && cy <= pb.y + pb.h) behind = p;
    }
    const bg = behind ? getComputedStyle(behind.el).fill : null;
    icons.push({href,
                x: +b.x.toFixed(2), y: +b.y.toFixed(2), w: +b.w.toFixed(2), h: +b.h.toFixed(2),
                cy: +(b.y + b.h / 2).toFixed(2), content,
                fillRgb: rgbOf(fill),
                bgRgb: rgbOf(bg && bg.startsWith('rgb') ? bg : null)});
  });

  // siblings: two boxes in the same group that sit side by side
  const parentIds = new Map();
  const siblings = [];
  document.querySelectorAll('rect, path').forEach(el => {
    if (el.closest('symbol, defs, marker')) return;
    if (parseFloat(el.getAttribute('opacity') || '1') < 0.5) return;
    if (el.tagName.toLowerCase() === 'path' && el.getAttribute('fill') === 'none') return;
    const b = el.getBoundingClientRect();
    if (b.width < 12 || b.height < 8) return;
    const parent = el.parentNode;
    if (!parentIds.has(parent)) parentIds.set(parent, parentIds.size);
    // a list of items carries labels; a drawing's parts do not
    const labelled = !!parent.querySelector('text');
    siblings.push({group: parentIds.get(parent), labelled,
                   x: +b.left.toFixed(2), y: +b.top.toFixed(2),
                   w: +b.width.toFixed(2), h: +b.height.toFixed(2)});
  });
  const symbols = [];
  document.querySelectorAll('symbol').forEach(sym => {
    // the union of every path the symbol draws
    let bb = null;
    sym.querySelectorAll('path, rect').forEach(p => {
      // clip paths and masks are definitions, not artwork
      if (p.closest('defs, clipPath, mask')) return;
      let t;
      try { t = p.getBBox(); } catch (e) { return; }
      if (!bb) bb = {x: t.x, y: t.y, w: t.width, h: t.height};
      else {
        const x = Math.min(bb.x, t.x), y = Math.min(bb.y, t.y);
        const r = Math.max(bb.x + bb.w, t.x + t.width), bo = Math.max(bb.y + bb.h, t.y + t.height);
        bb = {x, y, w: r - x, h: bo - y};
      }
    });
    if (!bb) return;
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

  return {vw: vb.width, vh: vb.height, texts, rects, frames, icons, symbols, movers, flowPaths, arrows, siblings};
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
            continue
        # a viewBox with empty space around the artwork makes the logo render
        # smaller than the box that was asked for
        if (bb["w"] < vb[2] * 0.85 or bb["h"] < vb[3] * 0.85):
            out.append(f"icon-padded  '{sym['id']}' draws only part of its viewBox, "
                       f"so it renders smaller than it looks")

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

    # a box that leaves no breathing room inside the frame around it
    for r in frames:
        cx, cy = r["x"] + r["w"] / 2, r["y"] + r["h"] / 2
        parents = [c for c in frames
                   if c is not r
                   and c["w"] >= r["w"] - 1 and c["h"] >= r["h"] - 1
                   and c["x"] <= cx <= c["x"] + c["w"] and c["y"] <= cy <= c["y"] + c["h"]]
        if not parents:
            continue
        frame = min(parents, key=lambda c: c["w"] * c["h"])
        # a band that spans the full width of its frame is a header, and a
        # header is meant to sit flush against the edge
        if abs(r["w"] - frame["w"]) < 1:
            continue
        pads = (r["x"] - frame["x"], (frame["x"] + frame["w"]) - (r["x"] + r["w"]),
                r["y"] - frame["y"], (frame["y"] + frame["h"]) - (r["y"] + r["h"]))
        if min(pads) < MIN_PADDING:
            out.append(f"tight-box    a box leaves only {min(pads):.0f}px of space "
                       f"inside the frame around it")

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

    # a logo painted in the colour of its background cannot be seen
    for ic in data.get("icons", []):
        if not ic.get("fillRgb") or not ic.get("bgRgb"):
            continue
        f, b = ic["fillRgb"], ic["bgRgb"]
        distance = sum((f[i] - b[i]) ** 2 for i in range(3)) ** 0.5
        if distance < 40:
            out.append(f"icon-faint   '{ic['href']}' is painted in almost the same "
                       f"colour as the shape behind it")

    # a logo too small to recognise is decoration, not a brand mark. A flat
    # mark (a cloud, a wordmark) is fine at a small height, so measure the
    # larger side and how much of the box the artwork actually fills.
    for ic in data.get("icons", []):
        c = ic.get("content")
        if not c or not c["w"] or not c["h"]:
            continue
        ink_w, ink_h = c["w"], c["h"]
        if max(ink_w, ink_h) < MIN_ICON_SIZE:
            out.append(f"icon-tiny    '{ic['href']}' draws {ink_w:.0f}×{ink_h:.0f}px, "
                       f"too small to recognise")
        elif ink_w * ink_h < ic["w"] * ic["h"] * MIN_ICON_FILL:
            out.append(f"icon-tiny    '{ic['href']}' fills only "
                       f"{ink_w * ink_h / (ic['w'] * ic['h']) * 100:.0f}% of its box, "
                       f"so it reads as a smudge")

    # an icon that runs into the label beside it reads as a smudge
    for ic in data.get("icons", []):
        if ic["w"] == 0:
            continue
        near = [t for t in data["texts"]
                if t["x"] < ic["x"] + ic["w"] and t["x"] + t["w"] > ic["x"]
                and abs(t["cy"] - ic["cy"]) < max(ic["h"], t["h"])]
        if near:
            out.append(f"icon-crowd   '{ic['href']}' overlaps '{near[0]['s']}'")

    # two items in the same group need room between them
    groups: dict = {}
    for b in data.get("siblings", []):
        groups.setdefault(b["group"], []).append(b)
    for boxes in groups.values():
        if len(boxes) < 3:
            continue          # two shapes side by side are a composition, not a list
        if not any(b.get("labelled") for b in boxes):
            continue          # the parts of one drawing, not a list of items
        for i, a in enumerate(boxes):
            for c in boxes[i + 1:]:
                # they overlap only if they overlap on both axes
                overlap_x = a["x"] < c["x"] + c["w"] and c["x"] < a["x"] + a["w"]
                overlap_y = a["y"] < c["y"] + c["h"] and c["y"] < a["y"] + a["h"]
                if overlap_x and overlap_y:
                    continue          # a band on a card, not two items
                dx = 0 if overlap_x else max(a["x"] - (c["x"] + c["w"]), c["x"] - (a["x"] + a["w"]))
                dy = 0 if overlap_y else max(a["y"] - (c["y"] + c["h"]), c["y"] - (a["y"] + a["h"]))
                gap = max(dx, dy)
                if gap < MIN_ITEM_GAP:
                    out.append(f"item-tight   two items in the same group are {gap:.0f}px apart")

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


class MeasurementError(RuntimeError):
    """The browser ran but the measurement itself failed."""


def check(svg: Path, port: int) -> list[str] | None:
    """Measure one SVG. ``None`` means the browser could not be driven.

    A browser that will not start is an environment problem and must not
    turn the build red. A browser that runs but whose measurement fails is a
    bug in this script, and that has to be reported rather than skipped.
    """
    try:
        cdp = CDP(svg.resolve().as_uri(), port=port)
    except Exception as exc:                     # no browser, or it refused to start
        print(f"  (browser unavailable: {exc})")
        return None
    try:
        cdp.call("Runtime.enable")
        return defects(cdp.evaluate(MEASURE_JS))
    except Exception as exc:
        raise MeasurementError(f"{svg.name}: measurement failed: {exc}") from exc
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
        try:
            found = check(svg, port=9510 + n)
        except MeasurementError as exc:
            print(f"  {exc}")
            failed = True
            continue
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
