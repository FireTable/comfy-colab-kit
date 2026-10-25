#!/usr/bin/env node
/**
 * DOT → geometry. Reads a graph description on stdin, writes the laid-out
 * node boxes and routed edge paths on stdout.
 *
 * Why this exists: hand-written diagram coordinates are the slow part of
 * authoring an SVG. Graphviz computes where every box goes and how every
 * line reaches it, so the drawing code only has to decide what a box looks
 * like — never where it sits.
 *
 * Input  (stdin):  {"dot": "<dot source>"}
 * Output (stdout): {"width":…, "height":…, "nodes": {id: {x,y,w,h}},
 *                   "edges": [{tail, head, d}]}
 *
 * Coordinates come back in CSS pixels (Graphviz reports points at 72 dpi;
 * SVG user units are 96 dpi).
 */
import { Graphviz } from "@hpcc-js/wasm-graphviz";

const PT = 4 / 3; // points → CSS pixels

const gv = await Graphviz.load();

/** The graph group carries the offset between Graphviz's space and the SVG's. */
function graphTransform(svg) {
  const m = svg.match(/<g id="graph0"[^>]*transform="[^"]*translate\(\s*(-?[\d.]+)[ ,]+(-?[\d.]+)\s*\)"/);
  return m ? [parseFloat(m[1]), parseFloat(m[2])] : [0, 0];
}

function polygonBox(points) {
  const nums = points.match(/-?\d*\.?\d+/g) || [];
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (let i = 0; i + 1 < nums.length; i += 2) {
    const x = +nums[i], y = +nums[i + 1];
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
  }
  return { x: minX, y: minY, w: maxX - minX, h: maxY - minY };
}

/** Shift a path's points by the graph offset and rescale to pixels. */
function transformPath(d, [tx, ty]) {
  // Graphviz writes absolute M/L/C commands; rewriting every pair is enough
  // because the path data has no relative commands and no arcs.
  let i = 0;
  const out = d.replace(/-?\d*\.?\d+(?:e[-+]?\d+)?/g, (m) => {
    const v = parseFloat(m);
    const isX = i % 2 === 0;
    i += 1;
    return String(isX ? (v + tx) * PT : (v + ty) * PT);
  });
  return out;
}

const input = JSON.parse(await readStdin());
const svg = gv.layout(input.dot, "svg", "dot");
const [tx, ty] = graphTransform(svg);

const nodes = {};
// <g id="nodeN" class="node"><title>name</title><polygon points="…"/>
const nodeRe = /<g id="node\d+" class="node">\s*<title>([^<]*)<\/title>([\s\S]*?)<\/g>/g;
let m;
while ((m = nodeRe.exec(svg)) !== null) {
  const id = m[1];
  const body = m[2];
  const poly = body.match(/<polygon[^>]*\spoints="([^"]+)"/);
  if (!poly) continue;
  const box = polygonBox(poly[1]);
  nodes[id] = {
    x: (box.x + tx) * PT,
    y: (box.y + ty) * PT,
    w: box.w * PT,
    h: box.h * PT,
  };
}

const edges = [];
// <g id="edgeN" class="edge"><title>tail->head</title><path d="…"/>
const edgeRe = /<g id="edge\d+" class="edge">\s*<title>([^<]*)<\/title>([\s\S]*?)<\/g>/g;
while ((m = edgeRe.exec(svg)) !== null) {
  // Graphviz escapes the arrow in an edge title
  const [tail, head] = m[1].replace(/&#45;/g, "-").replace(/&gt;/g, ">").split("->");
  const path = m[2].match(/<path[^>]*\sd="([^"]+)"/);
  if (!path) continue;
  edges.push({ tail, head, d: transformPath(path[1], [tx, ty]) });
}

const vb = svg.match(/viewBox="([^"]+)"/);
const dims = vb ? vb[1].trim().split(/\s+/).map(Number) : [0, 0, 0, 0];

process.stdout.write(JSON.stringify({
  width: dims[2] * PT,
  height: dims[3] * PT,
  nodes,
  edges,
}));

function readStdin() {
  return new Promise((resolve, reject) => {
    let buf = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (c) => (buf += c));
    process.stdin.on("end", () => resolve(buf));
    process.stdin.on("error", reject);
  });
}
