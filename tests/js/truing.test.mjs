// Truing: recover a known velocity and drag error from synthetic dope. Run: node --test tests/js/*.test.mjs
import {test} from "node:test";
import assert from "node:assert/strict";
import {createRequire} from "node:module";

const require = createRequire(import.meta.url);
const B = require("../../ballistics/static/solver.js");
const T = require("../../ballistics/static/truing.js");

const BASE = {bullet: {model: "G7", bc: 0.305, weightGr: 140, diameterMm: 6.71, lengthMm: 35.3}, mv: 823,
              sightHeightMm: 50, zeroRangeM: 100};
// "the rifle as it really is": 2 % faster and 6 % more drag than the profile says; dope read to 0.1 mil
const TRUE = {mvScale: 1.02, dragScale: 1.06};
const days = [{tempC: 15, hpa: 1013}, {tempC: -5, hpa: 995}, {tempC: 25, hpa: 1020}];
function dope(ranges, round = true) {
  return ranges.map((range, i) => {
    const input = Object.assign({}, BASE, {atmo: days[i % days.length]});
    const e = B.solve(Object.assign({}, input, TRUE), [range]).rows[0].elevMrad;
    return {id: "o" + range, input, range, elevMrad: round ? Math.round(e * 10) / 10 : e};
  });
}
const near = (a, b, tol, what) => assert.ok(Math.abs(a - b) <= tol, `${what}: ${a} vs ${b} (±${tol})`);

test("both: supersonic shots give the velocity, far shots the drag", () => {
  const r = T.fit(dope([300, 500, 600, 1000, 1100, 1200], false), "auto", {mvScale: 1, dragScale: 1});
  assert.equal(r.mode, "both");
  near(r.mvScale, TRUE.mvScale, 0.002, "mv scale"); near(r.dragScale, TRUE.dragScale, 0.01, "drag scale");
  assert.ok(r.rmsAfter < 0.01 && r.rmsBefore > 0.05, `rms ${r.rmsBefore} → ${r.rmsAfter}`);
});

test("rounded dope still trues to well under a click", () => {
  const r = T.fit(dope([400, 600, 1000, 1150]), "auto", {});
  assert.ok(r.rmsAfter < 0.06, `rms after ${r.rmsAfter}`);
  near(r.mvScale, TRUE.mvScale, 0.01, "mv scale");
});

test("supersonic shots only: velocity truing, and it fixes the dope there", () => {
  const r = T.fit(dope([300, 500, 650], false), "auto", {});
  assert.equal(r.mode, "velocity"); assert.equal(r.dragScale, 1);
  assert.ok(r.rmsAfter < 0.02, `rms after ${r.rmsAfter}`);
});

test("asking for both with one regime falls back and says why", () => {
  const r = T.fit(dope([300, 500], false), "both", {});
  assert.equal(r.mode, "velocity"); assert.ok(r.warnings.includes("both_needs_slow"));
});

test("drag only keeps the velocity; limits hold; short ranges warned", () => {
  const r = T.fit(dope([1000, 1100], false), "drag", {mvScale: 1.02});
  assert.equal(r.mode, "drag"); assert.equal(r.mvScale, 1.02);
  near(r.dragScale, TRUE.dragScale, 0.01, "drag scale");
  const wild = T.fit([{input: BASE, range: 600, elevMrad: 15}], "velocity", {});
  assert.equal(wild.mvScale, T.LIMITS.mvScale[0]); assert.ok(wild.warnings.includes("at_limit"));
  assert.ok(T.fit(dope([100, 200], false), "auto", {}).warnings.includes("short_range"));
});

test("an observation past the bullet's reach is skipped, not fatal", () => {
  const obs = dope([500], false).concat([{id: "far", input: BASE, range: 2900, elevMrad: 50}]);
  const r = T.fit(obs, "auto", {});
  assert.ok(r.warnings.includes("not_reached") || r.rows.find(x => x.id === "far").before != null);
  assert.equal(r.rows.length, 2);
});

test("speed: ten observations true in well under a second", () => {
  const obs = dope([300, 400, 500, 600, 700, 800, 900, 1000, 1100, 1200]);
  const t0 = performance.now(); T.fit(obs, "auto", {}); const ms = performance.now() - t0;
  assert.ok(ms < 1500, `${ms.toFixed(0)} ms`);
  console.log(`# fit of 10 observations: ${ms.toFixed(0)} ms`);
});
