// Reticle compiler and drawing. Run: node --test tests/js/*.test.mjs
import {test} from "node:test";
import assert from "node:assert/strict";
import {createRequire} from "node:module";

const require = createRequire(import.meta.url);
const R = require("../../ballistics/static/reticle.js");

const HASHES = {v: 1, unit: "MRAD", focal_plane: "FFP", field: 10, stadia: {w: 0.03, right: {to: 10}, left: {to: 10}, up: {to: 10}, down: {to: 10}},
  hashes: [{arms: ["left", "right", "up", "down"], from: 0.2, to: 8, step: 0.2, len: 0.1, major: {every: 5, len: 0.3}}]};

test("the compiler is deterministic", () => {
  assert.equal(JSON.stringify(R.compile(HASHES)), JSON.stringify(R.compile(JSON.parse(JSON.stringify(HASHES)))));
});

test("hash counts, positions and majors at every whole mil", () => {
  const {elements} = R.compile(HASHES);
  const marks = elements.filter(e => e.t === "line" && !(e.x1 === 0 && e.y1 === 0) && Math.abs(e.x1 - e.x2) + Math.abs(e.y1 - e.y2) < 1);
  assert.equal(marks.length, 160, "40 per arm × 4");
  const right = marks.filter(e => e.y1 !== e.y2 && e.x1 > 0);
  const len = e => Math.abs(e.y2 - e.y1);
  assert.equal(right.filter(e => len(e) > 0.2).map(e => e.x1).join(","), "1,2,3,4,5,6,7,8");
  assert.equal(right[0].x1, 0.2);
});

test("labels every N marks, text matches position", () => {
  const spec = Object.assign({}, HASHES, {hashes: [Object.assign({}, HASHES.hashes[0], {arms: ["right"], labels: {every: 10, size: 0.3}})]});
  const t = R.compile(spec).elements.filter(e => e.t === "text").map(e => e.text);
  assert.deepEqual(t, ["2", "4", "6", "8"]);
});

test("tree: linear half-width gives the outermost mark per row", () => {
  const spec = {stadia: {down: {to: 10}}, tree: {rows: {from: 1, to: 10, step: 1}, half_width: {kind: "linear", at_from: 1, at_to: 5},
    marks: {kind: "dot", step: 0.5}}};
  const dots = R.compile(spec).elements.filter(e => e.t === "dot");
  const outer = y => Math.max(...dots.filter(d => d.y === y).map(d => d.x));
  assert.equal(outer(1), 1); assert.equal(outer(10), 5);
  assert.ok(!dots.some(d => d.x === 0), "no dots on the vertical stadia");
});

test("mirror left/right copies the right arm", () => {
  const spec = {stadia: {w: 0.03, mirror: "lr", right: {to: 10, post: {from: 8, w: 0.25}}}, hashes: [{arms: ["left", "right"], from: 1, to: 5, step: 1, len: 0.2}]};
  const els = R.compile(spec).elements;
  const right = els.filter(e => (e.t === "line" && e.x1 > 0) || (e.t === "rect" && e.x > 0));
  const left = els.filter(e => (e.t === "line" && e.x1 < 0) || (e.t === "rect" && e.x < 0));
  assert.equal(left.length, right.length);
  const posts = els.filter(e => e.t === "poly");
  assert.equal(posts.length, 2);
  const xs = p => p.points.map(q => q[0]);
  const lp = posts.find(p => xs(p)[0] < 0), rp = posts.find(p => xs(p)[0] > 0);
  assert.equal(Math.min(...xs(lp)), -10); assert.equal(Math.max(...xs(rp)), 10);
  assert.deepEqual(xs(lp).map(x => -x), xs(rp), "left post is the right one mirrored");
});

test("gaps in the stadia, tapered posts and bars", () => {
  const {elements: e} = R.compile({stadia: {w: 0.035, right: {to: 12, segments: [[0.2, 0.4], [0.6, 2], [5, 6]],
    post: {from: 6, w: 0.5, taper: 0.5, bar: {at: 10, len: 1.5, w: 0.3}}}}});
  const lines = e.filter(x => x.t === "line").map(x => [x.x1, x.x2]);
  assert.deepEqual(lines, [[0.2, 0.4], [0.6, 2], [5, 6]]);
  const post = e.find(x => x.t === "poly");
  assert.deepEqual(post.points.slice(0, 3), [[6, 0.0175], [6.5, 0.25], [12, 0.25]], "pointed tip widening to 0.5 over 0.5");
  const bar = e.find(x => x.t === "rect");
  assert.deepEqual([bar.x, bar.y, bar.width, bar.height], [9.85, -0.75, 0.3, 1.5]);
});

test("tree: cleared centre, bigger whole-unit dots, numbers at a fixed spot", () => {
  const {elements: e} = R.compile({stadia: {down: {to: 10}}, tree: {rows: {from: 1, to: 2, step: 1},
    half_width: {kind: "list", values: [1, 2]}, inner: 0.4, marks: {kind: "dot", step: 0.2, r: 0.025, major: {every: 1, r: 0.04}},
    labels: {every: 1, x: 1.2, size: 0.3, texts: {"2": "X"}}}});
  const row2 = e.filter(x => x.t === "dot" && x.y === 2);
  assert.ok(!row2.some(d => Math.abs(d.x) < 0.4 - 1e-9), "nothing inside 0.4");
  assert.ok(!row2.some(d => d.x === 1.2), "no dot where the number is");
  assert.equal(row2.find(d => d.x === 2).r, 0.04); assert.equal(row2.find(d => d.x === 1.8).r, 0.025);
  assert.deepEqual(e.filter(x => x.t === "text").map(t => [t.text, t.x]), [["1", 1.2], ["X", 1.2]]);
});

test("hash numbers above a horizontal arm", () => {
  const {elements: e} = R.compile({stadia: {right: {to: 5}}, hashes: [{arms: ["right"], from: 1, to: 4, step: 1, len: 0.4,
    labels: {every: 2, size: 0.3, offset: 0.1, place: "before"}}]});
  const t = e.filter(x => x.t === "text");
  assert.deepEqual(t.map(x => x.text), ["2", "4"]);
  assert.ok(t.every(x => x.y < 0), "above the line");
});

test("every built-in template compiles cleanly", () => {
  for (const [k, t] of Object.entries(R.TEMPLATES)) {
    const res = R.compile(t.spec);
    assert.deepEqual(res.warnings, [], k);
    assert.ok(res.elements.length > 20 && res.elements.length < R.MAX, k);
  }
});

test("warnings: hashes past the arm end, SFP without design power", () => {
  const w = R.compile({focal_plane: "SFP", stadia: {right: {to: 5}}, hashes: [{arms: ["right"], from: 1, to: 8, step: 1}]}).warnings;
  assert.ok(w.some(x => x.includes("past its end")));
  assert.ok(w.some(x => x.includes("design magnification")));
});

test("SFP scaling: at half the design power a 1 mil mark covers 2 mil", () => {
  const ret = {focal_plane: "SFP", design_mag: 10};
  assert.equal(R.sfpFactor(ret, 5), 2);
  assert.equal(R.sfpFactor(ret, 10), 1);
  assert.equal(R.sfpFactor({focal_plane: "FFP", design_mag: 10}, 5), 1, "FFP ignores power");
});

test("unit conversion for holds on a reticle in another unit", () => {
  assert.ok(Math.abs(R.convert(1, "MRAD", "MOA") - 3.43775) < 1e-5);
  assert.ok(Math.abs(R.convert(R.convert(2.7, "MOA", "MRAD"), "MRAD", "MOA") - 2.7) < 1e-12);
  assert.equal(R.convert(1.5, "MRAD", "MRAD"), 1.5);
});

test("svg draws holds, pins off-view holds to the edge", () => {
  const out = R.svg(R.compile(HASHES).elements, {half: 5, holds: [{x: 1, y: 2, label: "T1"}, {x: 0, y: 9, label: "T2"}]});
  assert.ok(out.startsWith("<svg") && out.includes(">T1<") && out.includes("T2 ⇢"));
  assert.ok(!out.includes('cy="9"'), "pinned inside the view");
});

test("ranges for holdover marks, including both zero crossings", () => {
  // a rising-then-falling hold curve: under the line at the muzzle, crosses 0 near and far, then drops
  const rows = [];
  for (let r = 0; r <= 400; r += 5) rows.push({range: r, hold: 0.0002 * (r - 25) * (r - 50)});   // 0.25 below at the muzzle
  const res = R.rangesForHolds(rows, [0, 2, 4]);
  const zeros = res.filter(x => x.value === 0).map(x => Math.round(x.range));
  assert.deepEqual(zeros, [25, 50], "near and far zero");
  const two = res.find(x => x.value === 2);
  assert.ok(Math.abs(0.0002 * (two.range - 25) * (two.range - 50) - 2) < 0.05, "2 mil mark lands on the curve");
  assert.ok(res.find(x => x.value === 4).range > two.range, "further marks are further away");
  assert.ok(two.range > 50, "the falling bullet's crossing, not the rising one near the muzzle");
});

test("svg: view centre moves the window, scope draws a round field", () => {
  const out = R.svg([{t: "dot", x: 0, y: 0, r: 0.05}], {half: 2, cx: 1, cy: 3, scope: {r: 12}});
  assert.ok(out.includes('viewBox="-1 1 4 4"'));
  assert.ok(out.includes('clip-path="url(#rt-clip)"') && out.includes('r="12"'));
});
