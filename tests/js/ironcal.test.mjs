// Iron sight calibration: leaf angles from a caliper and/or a 100 m ladder, a diopter's true click.
import {test} from "node:test";
import assert from "node:assert/strict";
import {createRequire} from "node:module";

const require = createRequire(import.meta.url);
const C = require("../../ballistics/static/ironcal.js");
const near = (a, b, tol, msg) => assert.ok(Math.abs(a - b) <= tol, `${msg}: ${a} vs ${b}`);

// a synthetic leaf: true angles above setting 1 (mil); radius 620 mm
const TRUE = [0, 1.0, 2.2, 3.6, 5.2];
const R = 620;
const marks = (rise, poi) => TRUE.map((a, k) => Object.assign({label: String(k + 1), range_m: (k + 1) * 100},
  rise ? {rise_mm: 3 + a * R / 1000 * rise} : {}, poi ? {poi_cm: 15 + a * 10} : {}));

test("caliper alone: rise / radius", () => {
  const c = C.leafCal(marks(1, false), {radius_mm: R});
  TRUE.forEach((a, k) => near(c.rel[k], a, 1e-9, "setting " + (k + 1)));
  assert.equal(c.kFit, null); assert.ok(c.any);
});

test("shots alone at 100 m: cm above setting 1 / 10", () => {
  const c = C.leafCal(marks(false, true), {dist_m: 100});
  TRUE.forEach((a, k) => near(c.rel[k], a, 1e-9, "setting " + (k + 1)));
  assert.equal(c.src[2], "shot");
});

test("both: the caliper is scaled to the shots and fills the settings not shot", () => {
  // caliper reads 5 % low (e.g. the radius entered too long); only settings 1, 2 and 4 shot
  const ms = marks(0.95, true).map((m, k) => k === 2 || k === 4 ? Object.assign({}, m, {poi_cm: undefined}) : m);
  const c = C.leafCal(ms, {radius_mm: R});
  near(c.kFit, 1 / 0.95, 1e-9, "fit");
  near(c.rel[2], TRUE[2], 1e-9, "setting 3 from the scaled caliper");
  near(c.rel[4], TRUE[4], 1e-9, "setting 5 from the scaled caliper");
  assert.equal(c.src[4], "mech");
  assert.ok(!c.flags.some(Boolean));
  const raw = C.leafCal(ms, {radius_mm: R, fit: false});
  near(raw.rel[4], TRUE[4] * 0.95, 1e-9, "unscaled when the fit is off");
});

test("a setting where shots and caliper disagree is flagged", () => {
  const ms = marks(1, true); ms[3].poi_cm += 6;                // 0.6 mil off
  const c = C.leafCal(ms, {radius_mm: R});
  assert.ok(c.flags[3]);
});

test("needs the lowest setting and the radius", () => {
  const ms = marks(1, true); delete ms[0].rise_mm; delete ms[0].poi_cm;
  const c = C.leafCal(ms, {});
  assert.ok(c.warnings.includes("rise_needs_lowest") && c.warnings.includes("rise_needs_radius") && c.warnings.includes("poi_needs_lowest"));
  assert.ok(!c.any);
});

test("diopter: true click from a ladder", () => {
  // 0.47 MOA = 0.1367 mil per click; groups at 0, 10, 20 clicks at 100 m
  const mil = 0.47 / 3.43775;
  const c = C.clickCal([0, 10, 20].map((k, i) => ({clicks: k, poi_cm: 2 + k * mil * 10 + [0.3, -0.4, 0.1][i]})), 100);
  near(c.milPerClick, mil, 0.003, "click");
  assert.equal(C.clickCal([{clicks: 0, poi_cm: 1}], 100), null);
});
