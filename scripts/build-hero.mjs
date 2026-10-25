#!/usr/bin/env node
/**
 * Build the hero illustration for the architecture diagram.
 *
 * The hero is a real machine, not an icon. A request slug rides a rail from
 * the dispatch end to the compute head; when it arrives the head's lamp comes
 * up and the gauge needle swings over the CU it burned. The movement is the
 * explanation, so the drawing is built from parts that move: the carriage
 * slides, the needle turns, the lamp breathes.
 *
 * Geometry comes from the anatomy kit (scripts/kit/iso-kit.mjs, MIT): the kit
 * computes the isometric projection, the solid shading and the depth order,
 * so every part sits on another part and nothing passes through anything.
 *
 * The output is a standalone SVG with no script and no external reference.
 * It is inlined into assets/architecture/flow.svg, and it has to survive
 * GitHub's raw-file CSP, so it is vector only.
 *
 * Usage: node scripts/build-hero.mjs
 */
import { writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as k from "./kit/iso-kit.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = join(HERE, "..", "assets", "architecture", "hero.svg");

const WIDTH = 440;
const HEIGHT = 248;

/* ── the machine, in world units (x right, y into the page, z up) ─────────── */

const PLATE = { x: -104, y: -50, w: 208, d: 104, r: 8 };
const PLATE_H = 9;
const FOOT_H = 5;

const RAIL_FROM = -76;
const RAIL_TO = 80;
const RAIL_Y = [-15, 11];
const RAIL_W = 4;
const RAIL_H = 3;

const CARRIAGE = { x: -22, y: -20, w: 44, d: 38, r: 4 };
const CARRIAGE_Z = RAIL_H;
const CARRIAGE_TOP = CARRIAGE_Z + 6;
const SLUG = { x: -8, y: -6, w: 16, d: 12, r: 2 };
const SLUG_H = 7;

// where the carriage starts and where it has to end up
const RUN_FROM = -58;
const RUN_TO = 38;

const HEAD_X = 80;
const HEAD_BLOCK = { x: HEAD_X, y: -18, w: 28, d: 40, r: 3 };
const HEAD_Z = 26;
const HEAD_H = 13;
const FINS = 5;

// the terminal the request is posted into
const TERMINAL = { x: -92, y: -18, w: 16, d: 40, r: 2 };

const DIAL = [-72, 30];
const DIAL_R = 16;
const DIAL_Z = 50;
const DIAL_H = 9;
const DIAL_TOP = DIAL_Z + DIAL_H;
const POST_R = 4;
const LAMP_Z = DIAL_Z + 24;

const P = k.fitProjection(
  [
    ...k.boxCorners(PLATE, -PLATE_H - FOOT_H, 0),
    [DIAL[0], DIAL[1], LAMP_Z + 9],
    [HEAD_X + HEAD_BLOCK.w, 20, HEAD_Z + HEAD_H + 8],
    [RUN_FROM, 0, CARRIAGE_TOP + SLUG_H],
  ],
  WIDTH,
  HEIGHT,
  { pad: 18, azimuth: 40, elevation: 24 },
);

const at = (point) => k.iso(point, P);
const S = (paths, style) => k.solidSvg(paths, style);
const L = (d, style) => k.lineSvg(d, style);
const D = (points, style) => k.dotsSvg(points, style);
const byDepth = (points) =>
  [...points].sort((a, b) => k.depthOf([a[0], a[1], 0], P) - k.depthOf([b[0], b[1], 0], P));

/* ── the base ─────────────────────────────────────────────────────────────── */

function plate() {
  const out = [`<path class="iso-halo" d="${k.haloOf(PLATE, -PLATE_H - FOOT_H, PLATE_H, P)}"/>`];
  for (const [x, y] of byDepth(k.corners(PLATE, 22))) {
    out.push(S(k.cylinder(x, y, 6.5, -PLATE_H - FOOT_H, 1.6, P,13), { tone: "lo" }));
    out.push(S(k.cylinder(x, y, 4.8, -PLATE_H - FOOT_H + 1.6, FOOT_H - 1.6, P,13), { tone: "mid" }));
  }
  out.push(S(k.slabOf(PLATE, -PLATE_H, PLATE_H, P, 8,4), { tone: "mid" }));
  out.push(L(k.planOutline(k.insetPlan(PLATE, 5), 0, P), { tone: "lo" }));

  // corner screws
  const screws = k.corners(PLATE, 10);
  out.push(L(screws.map(([x, y]) => k.ring(x, y, 2.1, 0, P,8)).join(""), { tone: "lo" }));
  out.push(D(screws.map(([x, y]) => at([x, y, 0])), { size: 0.5 }));

  // a label plate on the front left
  const tag = { x: PLATE.x + 12, y: PLATE.y + PLATE.d - 20, w: 52, d: 11, r: 1.5 };
  out.push(S(k.slabOf(tag, 0, 1.2, P, 3,4), { tone: "lo" }));
  out.push(L(k.planOutline(k.insetPlan(tag, 2.4), 1.2, P, 3), { tone: "faint" }));

  // a ruler strip along the front edge: the machine reads its own travel
  const strip = { x: PLATE.x + 72, y: PLATE.y + PLATE.d - 18, w: 118, d: 8, r: 1 };
  out.push(S(k.slabOf(strip, 0, 0.6, P,4), { tone: "lo", crease: "none" }));
  const ticks = k.topTicks(strip.x + 4, strip.x + strip.w - 4, 4, 5, strip.y + 0.8, 0.6, [2, 3.6], P);
  out.push(L(ticks.minor, { tone: "lo" }), L(ticks.major, { tone: "mid" }));
  out.push(D(
    [strip.x + 2, strip.x + strip.w - 2].map((x) => at([x, strip.y + 3.6, 0.6])),
    { size: 0.45 },
  ));
  return out.join("");
}

/* ── the rail the request rides ───────────────────────────────────────────── */

function terminal() {
  const out = [];
  // the block the request is posted into, with two binding posts
  out.push(S(k.slabOf(TERMINAL, 0, 14, P, 3,4), { tone: "mid" }));
  out.push(L(k.planOutline(k.insetPlan(TERMINAL, 2.4), 14, P, 3), { tone: "faint" }));
  for (const y of [-9, 7]) {
    out.push(S(k.cylinder(TERMINAL.x + 8, y, 2.6, 14, 5, P,10), { tone: "hi", crease: "none" }));
    out.push(L(k.ring(TERMINAL.x + 8, y, 2.6, 19, P,10), { tone: "lo" }));
  }
  out.push(D([at([TERMINAL.x + 8, -9, 19]), at([TERMINAL.x + 8, 7, 19])], { size: 0.45, tone: "hi" }));
  return out.join("");
}

function rails() {
  const out = [];
  for (const y of RAIL_Y) {
    const plan = { x: RAIL_FROM, y, w: RAIL_TO - RAIL_FROM, d: RAIL_W, r: 1 };
    out.push(S(k.slabOf(plan, 0, RAIL_H, P, 3,4), { tone: "mid" }));
    out.push(D([RAIL_FROM + 7, RAIL_TO - 7].map((x) => at([x, y + RAIL_W / 2, RAIL_H])), { size: 0.4 }));
  }
  // end stops
  for (const x of [RAIL_FROM - 6, RAIL_TO]) {
    out.push(S(k.slabOf({ x, y: -22, w: 6, d: 48, r: 1.2 }, 0, 8, P, 3,4), { tone: "mid" }));
    out.push(D([at([x + 3, -16, 8]), at([x + 3, 12, 8])], { size: 0.45 }));
  }
  // a cable running under the rail, into the head
  out.push(L(k.segment([RAIL_FROM - 2, -26, 1], [HEAD_X + 12, -26, 1], P), { tone: "faint" }));
  out.push(D([at([RAIL_FROM - 2, -26, 1]), at([HEAD_X + 12, -26, 1])], { size: 0.45, tone: "faint" }));
  return out.join("");
}

/* ── the carriage and the request it carries ──────────────────────────────── */

function carriage() {
  const out = [];
  out.push(S(k.slabOf(CARRIAGE, CARRIAGE_Z, 6, P, 5,4), { tone: "hi" }));
  // bushings riding the two rails
  for (const x of [CARRIAGE.x + 8, CARRIAGE.x + CARRIAGE.w - 8]) {
    for (const y of RAIL_Y) {
      out.push(S(k.cylinder(x, y + RAIL_W / 2, 2.6, RAIL_H, 2.4, P,10), { tone: "lo", crease: "none" }));
    }
  }
  out.push(L(k.lineOnTop([CARRIAGE.x, -2], [CARRIAGE.x + CARRIAGE.w, -2], CARRIAGE_TOP, P), { tone: "lo" }));

  // the request slug: the one lit part of the machine
  out.push(S(k.slabOf(SLUG, CARRIAGE_TOP, SLUG_H, P, 3,4), { tone: "hi", lit: true, className: "hero-slug" }));
  out.push(L(k.planOutline(k.insetPlan(SLUG, 2.4), CARRIAGE_TOP + SLUG_H, P, 3), { tone: "faint" }));

  // a spring that pushes the slug forward
  out.push(L(k.coil([CARRIAGE.x + CARRIAGE.w - 4, -2, CARRIAGE_TOP + 1],
                    [CARRIAGE.x + CARRIAGE.w - 4, -2, CARRIAGE_TOP + 9],
                    [CARRIAGE.x + CARRIAGE.w - 4, -2, CARRIAGE_TOP + 5], 2.4, 3, "z", P, 14),
    { tone: "lo" }));

  // a knurled knob the operator would push it with
  const knob = [CARRIAGE.x + CARRIAGE.w - 7, CARRIAGE.y + CARRIAGE.d + 4];
  out.push(S(k.cylinder(knob[0], knob[1], 4.4, CARRIAGE_Z, 5, P,13), { tone: "mid" }));
  out.push(L(k.knurl(knob[0], knob[1], 4.4, CARRIAGE_Z + 5, CARRIAGE_Z + 5, 26, P), { tone: "lo" }));
  out.push(D([at([knob[0], knob[1], CARRIAGE_Z + 5])], { size: 0.5, tone: "hi" }));
  return out.join("");
}

/* ── the compute head: the accelerator the request is going to ────────────── */

function head() {
  const out = [];
  // two posts and a crossbar carry the block over the rail end
  for (const y of [-18, 16]) {
    out.push(S(k.slabOf({ x: HEAD_X - 4, y, w: 6, d: 8, r: 1.2 }, 0, HEAD_Z, P, 3,4), { tone: "mid" }));
    out.push(D([at([HEAD_X - 1, y + 4, HEAD_Z])], { size: 0.45 }));
  }
  out.push(S(k.slabOf({ x: HEAD_X - 6, y: -20, w: 10, d: 44, r: 1.5 }, HEAD_Z, 5, P, 3,4), { tone: "mid" }));
  // the block itself
  out.push(S(k.slabOf(HEAD_BLOCK, HEAD_Z + 5, HEAD_H, P, 4,4), { tone: "hi" }));
  out.push(L(k.planOutline(k.insetPlan(HEAD_BLOCK, 3), HEAD_Z + 5 + HEAD_H, P, 4), { tone: "faint" }));
  // heat-sink fins
  for (let i = 0; i < FINS; i += 1) {
    const x = HEAD_BLOCK.x + 4 + i * 4.6;
    out.push(S(k.slabOf({ x, y: HEAD_BLOCK.y + 3, w: 2.4, d: HEAD_BLOCK.d - 6, r: 0.6 },
      HEAD_Z + 5 + HEAD_H, 3.4, P, 2,4), { tone: "mid", crease: "none" }));
  }
  // the connector strip along the front
  out.push(S(k.slabOf({ x: HEAD_BLOCK.x + 3, y: HEAD_BLOCK.y - 3, w: HEAD_BLOCK.w - 6, d: 4, r: 0.8 },
    HEAD_Z + 6, 2, P, 2,4), { tone: "lo", crease: "none" }));
  out.push(D([at([HEAD_BLOCK.x + 6, HEAD_BLOCK.y - 1, HEAD_Z + 8]),
    at([HEAD_BLOCK.x + HEAD_BLOCK.w - 6, HEAD_BLOCK.y - 1, HEAD_Z + 8])], { size: 0.4, tone: "faint" }));
  // the lamp that comes up when the work is done
  out.push(S(k.cylinder(HEAD_BLOCK.x + 5, HEAD_BLOCK.y + 5, 2.2, HEAD_Z + 5 + HEAD_H + 3.4, 2.4, P,10),
    { tone: "hi", lit: true, className: "hero-led" }));
  return out.join("");
}

/* ── the gauge and the lamp that waits for the request ─────────────────────── */

function gauge() {
  const out = [];
  const [x, y] = DIAL;
  // post, clamp, thumb screw
  out.push(S(k.cylinder(x, y, POST_R, 0, DIAL_Z - 6, P,13), { tone: "mid" }));
  out.push(L(k.sideArc(x, y, POST_R, 8, P, 14), { tone: "faint" }));
  const clamp = { x: x - 7, y: y - 7, w: 14, d: 14, r: 2.5 };
  out.push(S(k.slabOf(clamp, DIAL_Z - 6, 11, P, 4,4), { tone: "mid" }));
  const thumb = [x + 6, y + 9];
  out.push(S(k.cylinder(thumb[0], thumb[1], 2.6, DIAL_Z - 6, 6, P,10), { tone: "mid" }));
  out.push(L(k.knurl(thumb[0], thumb[1], 2.6, DIAL_Z - 6, DIAL_Z, 18, P), { tone: "lo" }));

  // the dial face
  out.push(S(k.cylinder(x, y, 3.2, DIAL_Z - 12, DIAL_Z - 12 + 12, P,11), { tone: "mid" }));
  out.push(S(k.cylinder(x, y, DIAL_R, DIAL_Z, DIAL_H, P, 28,4), { tone: "hi", crease: "lo" }));
  out.push(L(k.knurl(x, y, DIAL_R, DIAL_TOP - 3, DIAL_TOP, 64, P,4), { tone: "lo" }));
  out.push(L(k.sideArc(x, y, DIAL_R, DIAL_TOP - 3, P, 40), { tone: "faint" }));
  out.push(L(k.ring(x, y, DIAL_R - 2, DIAL_TOP, P,28), { tone: "lo" }));
  const ticks = k.radialTicks(x, y, DIAL_R - 3, 40, 5, DIAL_TOP, [1.2, 2.6], P, -Math.PI * 0.78, Math.PI * 1.56);
  out.push(L(ticks.minor, { tone: "lo" }), L(ticks.major, { tone: "mid" }));
  out.push(L(k.ring(x, y, 1.6, DIAL_TOP, P,8), { tone: "mid" }));

  // the lamp on top of the post: it waits for the request
  out.push(S(k.cylinder(x, y, 6, LAMP_Z, 5, P,12), { tone: "mid" }));
  out.push(S(k.cylinder(x, y, 4.6, LAMP_Z + 5, 4, P, 24,4), { tone: "hi", lit: true, className: "hero-lamp" }));
  out.push(L(k.ring(x, y, 6, LAMP_Z, P,12), { tone: "lo" }));
  return out.join("");
}

/* ── the needle, drawn at rest and turned by CSS ──────────────────────────── */

const needleAngle = (turn) => {
  const angle = -Math.PI * 0.62 + turn * Math.PI * 1.24;
  const [x, y] = DIAL;
  return k.lineOnTop(
    [x - Math.sin(angle) * 3, y + Math.cos(angle) * 3],
    [x + Math.sin(angle) * (DIAL_R - 3.5), y - Math.cos(angle) * (DIAL_R - 3.5)],
    DIAL_TOP + 0.3,
    P,
  );
};

/* ── assemble ─────────────────────────────────────────────────────────────── */

const start = k.iso([0, 0, 0], P);
const end = k.iso([RUN_TO - RUN_FROM, 0, 0], P);
const travelX = (end[0] - start[0]).toFixed(2);
const travelY = (end[1] - start[1]).toFixed(2);

const pivot = (() => {
  const d = needleAngle(0.08);
  const [x, y] = d.slice(1).split("L")[0].trim().split(/\s+/).map(Number);
  return [x, y];
})();

const body = [
  plate(),
  rails(),
  terminal(),
  gauge(),
  `<g class="hero-carriage" style="--tx:${travelX}px;--ty:${travelY}px">${carriage()}</g>`,
  head(),
  `<path class="iso-line hero-needle" data-tone="mid" data-free="start" d="${needleAngle(0.08)}" style="stroke-width:1.6">
    <animateTransform attributeName="transform" type="rotate"
      values="0 ${pivot[0]} ${pivot[1]};0 ${pivot[0]} ${pivot[1]};74 ${pivot[0]} ${pivot[1]};74 ${pivot[0]} ${pivot[1]}"
      keyTimes="0;0.08;0.62;1" dur="6.4s" repeatCount="indefinite" calcMode="spline"
      keySplines="0.32 0.72 0 1;0.32 0.72 0 1;0 0 1 1"/>
  </path>`,
].join("");

const svg = k.figureSvg({
  width: WIDTH,
  height: HEIGHT,
  label:
    "A dispatch bench. A request slug rides a carriage along a rail toward a finned compute head. When it arrives, the head's lamp comes up and the gauge needle swings over the compute it burned.",
  body,
  standalone: "dark",
});

// The kit's own palette is a grey machine on paper. This one sits on the
// diagram's plum panel, so the roles are remapped to the project colours:
// plum and violet for the body, amber for the outlines, lime for the one lit
// part, green for the lamp on the head.
const PALETTE = `
.iso{
  /* brushed metal on the plum panel: the body stays light so the machine reads
     at a glance, and only the request, the head lamp and the waiting lamp take
     the project colours */
  --anatomy-paper:#c9c2b4;--anatomy-face:#d8d1c2;--anatomy-top:#e6dfcf;
  --anatomy-shade-0:#6e675c;--anatomy-shade-1:#857d70;--anatomy-shade-2:#9c9384;--anatomy-shade-3:#b3a998;
  --anatomy-lit-top:#f2ff59;--anatomy-lit-shade:#8a9a24;
  --anatomy-hi:#fbf6ea;--anatomy-mid:#4a4338;--anatomy-lo:#6b6254;--anatomy-faint:#8d8474;
  --anatomy-lit:#f2ff59;
  --anatomy-halo:#100c18;--anatomy-halo-opacity:.55;--anatomy-dot:#3a342c;
  --anatomy-red:#f9ab00;--anatomy-green:#1f8f52;--anatomy-blue:#f2ff59;
  --anatomy-weight:.6px;--anatomy-ease:cubic-bezier(.32,.72,0,1);
}
.iso-svg .iso-halo{filter:none}
/* the carriage is a touch lighter than the bed it rides on */
.hero-carriage .iso-solid>.iso-fill{fill:#ddd6c6}
.hero-carriage .iso-solid>.iso-top{fill:#efe8d8}
.hero-carriage .iso-solid>.iso-shade{fill:#b7af9e}
.iso-solid.hero-slug>.iso-top{fill:#f2ff59}
.iso-solid.hero-slug>.iso-fill{fill:#3a4a1e}
.iso-solid.hero-led>.iso-top{fill:#3ddc84}
.iso-solid.hero-led>.iso-fill{fill:#123322}
.iso-solid.hero-lamp>.iso-top{fill:#f9ab00}
.iso-solid.hero-lamp>.iso-fill{fill:#3a2a10}
`;

// The machine moves: the carriage slides to the head, the needle swings over
// with it, the head lamp comes up and the waiting lamp at the far end goes
// out. Nothing is scripted, so it still runs in a README <img>.
const MOTION = `
@keyframes hero-travel{
  0%{transform:translate(0,0)}
  5%{transform:translate(0,0)}
  62%{transform:translate(var(--tx),var(--ty))}
  92%{transform:translate(var(--tx),var(--ty))}
  100%{transform:translate(var(--tx),var(--ty))}
}
@keyframes hero-fade{
  0%{opacity:0}
  5%{opacity:1}
  88%{opacity:1}
  100%{opacity:0}
}
@keyframes hero-led{
  0%,45%{opacity:.25}
  60%,100%{opacity:1}
}
@keyframes hero-lamp{
  0%,20%{opacity:1}
  34%,100%{opacity:.2}
}
.hero-carriage{animation:hero-travel 6.4s cubic-bezier(.32,.72,0,1) infinite,hero-fade 6.4s linear infinite}
.iso-solid.hero-led{animation:hero-led 2.2s ease-in-out infinite}
.iso-solid.hero-lamp{animation:hero-lamp 6.4s linear infinite}
@media (prefers-reduced-motion: reduce){
  .hero-carriage,.iso-solid.hero-led,.iso-solid.hero-lamp{animation:none}
  .hero-needle animateTransform{display:none}
}
`;

const out = svg
  .replace("</svg>", `<style>${PALETTE}${MOTION}</style></svg>`)
  .replace('class="iso-svg iso"', 'class="iso-svg iso" style="background:#1b1523"');

// The kit writes full-precision coordinates and a lot of whitespace. Round the
// numbers and drop the padding: the drawing is the same, the file is half.
const slim = out
  .replace(/(?<!values="[^"]{0,400})(\d+\.\d{3,})/g, (m) => String(Math.round(Number(m) * 10) / 10))
  .replace(/>\s+</g, "><")
  .replace(/\s{2,}/g, " ")
  .replace(/\s(?=[^<])/g, " ")
  .trim();

writeFileSync(OUT, slim);
console.log(`wrote ${OUT} (${(slim.length / 1024).toFixed(1)} KB, was ${(out.length / 1024).toFixed(1)} KB)`);
