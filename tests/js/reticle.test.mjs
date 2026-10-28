// Reticle compiler and drawing. Run: node --test tests/js/*.test.mjs
import {test} from "node:test";
import assert from "node:assert/strict";
import {createRequire} from "node:module";

const require = createRequire(import.meta.url);
const R = require("../../diary/static/ballistics/reticle.js");

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
  const post = els.filter(e => e.t === "rect");
  assert.deepEqual(post.map(r => [r.x, r.width]).sort(), [[-10, 2], [8, 2]]);
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
