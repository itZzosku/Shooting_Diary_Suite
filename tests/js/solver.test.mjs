// Ballistic solver tests: golden cases against an independent solver, plus physical properties.
// Run: node --test tests/js
import {test} from "node:test";
import assert from "node:assert/strict";
import {createRequire} from "node:module";

const require = createRequire(import.meta.url);
const B = require("../../diary/static/ballistics/solver.js");
const golden = require("./golden.json");

const BULLET = {model: "G7", bc: 0.305, weightGr: 140, diameterMm: 6.71, lengthMm: 35.3};
const BASE = {bullet: BULLET, mv: 823, sightHeightMm: 50, zeroRangeM: 100};
const at = (inp, range) => B.solve(inp, [range]).rows[0];
const near = (a, b, tol, msg) => assert.ok(Math.abs(a - b) <= tol, `${msg}: ${a} vs ${b} (±${tol})`);

// ------------------------------------------------------------------ golden cases
for (const c of golden.cases) {
  test(`golden: ${c.name}`, () => {
    const res = B.solve(c.input, c.expected.map(e => e.range));
    c.expected.forEach((e, i) => {
      const o = res.rows[i];
      assert.ok(o.reached, `reached ${e.range} m`);
      const tol = e.range <= 600 ? 0.02 : 0.05;
      near(o.elevMrad, e.elevMrad, tol, `elevation at ${e.range} m`);
      if (e.range <= 1000) near(o.windMrad, e.windMrad, 0.02, `windage at ${e.range} m`);
      near(o.tof, e.tof, e.tof * 0.005, `time of flight at ${e.range} m`);
      near(o.v, e.v, e.v * 0.005, `velocity at ${e.range} m`);
    });
  });
}

// ------------------------------------------------------------------ properties
test("hold at the zero range is 0 in zero conditions", () => {
  near(at(BASE, 100).elevMrad, 0, 1e-4, "100 m zero");
  near(at(Object.assign({}, BASE, {zeroRangeM: 300}), 300).elevMrad, 0, 1e-4, "300 m zero");
});

test("colder or denser air and lower velocity mean more drop", () => {
  const std = at(BASE, 800).elevMrad;
  assert.ok(at(Object.assign({}, BASE, {atmo: {tempC: -15, hpa: 1013.25}}), 800).elevMrad > std, "cold");
  assert.ok(at(Object.assign({}, BASE, {atmo: {tempC: 15, hpa: 1040}}), 800).elevMrad > std, "high pressure");
  assert.ok(at(Object.assign({}, BASE, {mv: 850}), 800).elevMrad < std, "faster");
});

test("no wind, no spin drift, no Coriolis → no windage", () => {
  near(at(BASE, 1000).windMrad, 0, 1e-9, "windage");
});

test("left and right wind mirror each other", () => {
  const l = at(Object.assign({}, BASE, {wind: {ms: 4, clock: 9}}), 800).windMrad;
  const r = at(Object.assign({}, BASE, {wind: {ms: 4, clock: 3}}), 800).windMrad;
  assert.ok(l < 0 && r > 0, "wind from the left is held left (negative)");
  near(l, -r, 1e-6, "mirror");
});

test("wind drift is close to linear in wind speed (scaled wind card)", () => {
  const one = at(BASE, 800).windPer1Mrad;
  const two = Math.abs(at(Object.assign({}, BASE, {wind: {ms: 2, clock: 9}}), 800).windMrad);
  near(two, 2 * one, 0.01, "2 m/s vs 2 × 1 m/s");
});

test("incline: holds less than flat, loses more speed uphill than downhill", () => {
  const flat = at(BASE, 800), up = at(Object.assign({}, BASE, {angleDeg: 25}), 800),
        down = at(Object.assign({}, BASE, {angleDeg: -25}), 800);
  assert.ok(up.elevMrad < flat.elevMrad && down.elevMrad < flat.elevMrad, "both hold less than flat");
  assert.ok(up.v < flat.v && down.v > flat.v, "gravity along the line of sight");
  // improved rifleman's rule: gravity drop scales with cos(angle) – within 0.15 mil at 800 m
  near(up.calmElevMrad, flat.elevMrad * Math.cos(25 * Math.PI / 180), 0.15, "≈ cos rule");
});

test("powder temperature sensitivity changes velocity linearly", () => {
  const inp = Object.assign({}, BASE, {mvTempC: 15, tempSens: 0.8});
  near(B.mvAt(inp, 25), 831, 1e-9, "+10 °C");
  near(B.mvAt(inp, -5), 807, 1e-9, "−20 °C");
  assert.equal(B.mvAt(Object.assign({}, BASE), 30), 823, "no slope → no change");
});

test("multi-BC bands pick the band by speed", () => {
  const banded = Object.assign({}, BASE, {bullet: Object.assign({}, BULLET, {bands: [[0, 0.290], [600, 0.305]]})});
  const r = at(banded, 1000), plain = at(BASE, 1000);
  assert.ok(r.elevMrad > plain.elevMrad, "lower BC below 600 m/s → more drop");
});

test("spin drift goes with the twist and grows with range", () => {
  const sd = Object.assign({}, BASE, {twistMm: 203, spinDrift: true});
  const r5 = at(sd, 500).windMrad, r10 = at(sd, 1000).windMrad;
  assert.ok(r5 < 0 && r10 < r5, "right twist drifts right → hold left, more at 1000 m");
  assert.ok(at(Object.assign({}, sd, {twistDir: "L"}), 1000).windMrad > 0, "left twist the other way");
  const sg = B.stability(BULLET, 203, 823, B.STD);
  assert.ok(sg > 1.4 && sg < 3, `stability factor ${sg}`);
});

test("aerodynamic jump: right twist, wind from the left lifts the impact", () => {
  const aj = Object.assign({}, BASE, {twistMm: 203, aeroJump: true});
  const left = at(Object.assign({}, aj, {wind: {ms: 5, clock: 9}}), 600).elevMrad;
  const right = at(Object.assign({}, aj, {wind: {ms: 5, clock: 3}}), 600).elevMrad;
  assert.ok(left < right, "lifted impact → less hold");
});

test("Coriolis is small and depends on azimuth", () => {
  const base = Object.assign({}, BASE, {coriolis: true, latitude: 63});
  const east = at(Object.assign({}, base, {azimuthDeg: 90}), 1000).elevMrad;
  const west = at(Object.assign({}, base, {azimuthDeg: 270}), 1000).elevMrad;
  assert.ok(east < west, "Eötvös: firing east hits high");
  assert.ok(Math.abs(east - west) < 0.2, "and the effect stays small");
  const north = at(Object.assign({}, base, {azimuthDeg: 0}), 1000).windMrad;
  assert.ok(north < 0 && north > -0.15, "northern hemisphere: drifts right, held left");
});

test("units, clicks and turns", () => {
  near(B.toUnit(1, "MOA"), 3.43775, 1e-5, "1 mrad in MOA");
  assert.deepEqual(B.dial(4.33, {click: 0.1}), {clicks: 43, value: 4.3});
  const t = B.dial(12.34, {click: 0.1, turn: 10});
  assert.equal(t.turns, 1); near(t.rest, 2.3, 1e-9, "rest");
  assert.equal(B.dial(-0.26, {click: 0.25}).clicks, -1);
});

test("station pressure from sea-level pressure", () => {
  near(B.stationFromSeaLevel(1013.25, 0, 15), 1013.25, 1e-9, "sea level");
  const p = B.stationFromSeaLevel(1013.25, 1000, 8.5);
  assert.ok(p > 895 && p < 902, `1000 m: ${p}`);
});

test("speed: a 1200 m solution well under a frame on a desktop", () => {
  B.solve(BASE, [100, 1200]);                    // warm up
  const t0 = performance.now();
  for (let i = 0; i < 10; i++) B.solve(BASE, [1200]);
  const ms = (performance.now() - t0) / 10;
  assert.ok(ms < 30, `${ms.toFixed(1)} ms per solve`);
});
