/* Iron sight calibration: what each setting of a leaf (or each click of a diopter) really does, in mil.
   Pure functions, no DOM: loaded as a plain script (window.IronCal) or with require().

   Two ways to measure a leaf, both giving each setting's angle above the lowest setting:
   - mechanically: how far the rear notch rises (mm, with a caliper or height gauge) ÷ the sight radius (m) = mil;
   - shooting: the profile's groups (where they landed on each setting, saved with the load), turned by the page
     into the angle each setting actually points at ("angle", mil); setting 1's angle is the reference.
   With both, the caliper values are fitted to the shots with one scale factor (a slightly wrong sight radius, a
   slider that seats differently under recoil), so settings not shot still get a value from the caliper. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.IronCal = factory();
})(this, function () {
  "use strict";
  const has = x => x != null && x !== "" && isFinite(+x);
  const FLAG_MIL = 0.3;          // shots and caliper disagree by more than a 100 m group's centre is usually off

  /** marks: [{label, range_m, rise_mm?, angle?}] (any order), opts {radius_mm, fit (true)}
      → {rel: [mil|null] in range order (0 for the lowest setting), labels, mech, shot, src: ["shot"|"mech"|null],
         k (scale applied to the caliper), kFit (fitted, or null), rms (shots vs scaled caliper, mil), flags: [bool],
         warnings: [codes], any (some setting measured)} */
  function leafCal(marks, opts) {
    opts = opts || {};
    // the leaf's order: by slot number when every label is one, else by range
    const ms = (marks || []).slice().sort((marks || []).every(m => /^\d+$/.test(m.label))
      ? (a, b) => a.label - b.label : (a, b) => a.range_m - b.range_m);
    const R = has(opts.radius_mm) && +opts.radius_mm > 0 ? +opts.radius_mm / 1000 : null;
    const warnings = [];
    const anyRise = ms.some(m => has(m.rise_mm)), anyPoi = ms.some(m => has(m.angle));
    let baseRise = ms.length && has(ms[0].rise_mm) ? +ms[0].rise_mm : null;
    const basePoi = ms.length && has(ms[0].angle) ? +ms[0].angle : null;
    if (anyRise && baseRise == null) warnings.push("rise_needs_lowest");
    if (anyRise && !R) warnings.push("rise_needs_radius");
    if (anyPoi && basePoi == null) warnings.push("poi_needs_lowest");
    const mech = ms.map(m => R && baseRise != null && has(m.rise_mm) ? (+m.rise_mm - baseRise) / R : null);
    const shot = ms.map(m => basePoi != null && has(m.angle) ? +m.angle - basePoi : null);

    // one scale on the caliper values, fitted to the shots (least squares through 0)
    let num = 0, den = 0, n = 0;
    for (let i = 1; i < ms.length; i++) if (mech[i] != null && shot[i] != null && Math.abs(mech[i]) > 0.3) {
      num += mech[i] * shot[i]; den += mech[i] * mech[i]; n++;
    }
    const kFit = n ? num / den : null;
    if (kFit != null && (kFit < 0.85 || kFit > 1.15)) warnings.push("fit_far");
    const k = kFit != null && opts.fit !== false ? kFit : 1;
    let ss = 0;
    const flags = ms.map((m, i) => {
      if (mech[i] == null || shot[i] == null || i === 0) return false;
      const d = shot[i] - k * mech[i]; ss += d * d;
      return Math.abs(d) > FLAG_MIL;
    });
    const rms = n ? Math.sqrt(ss / n) : null;
    const rel = ms.map((m, i) => shot[i] != null ? shot[i] : mech[i] != null ? k * mech[i] : null);
    const src = ms.map((m, i) => shot[i] != null ? "shot" : mech[i] != null ? "mech" : null);
    const any = rel.some((x, i) => x != null && i > 0);
    if (any) { rel[0] = 0; src[0] = src[0] || "base"; }
    return {rel, labels: ms.map(m => m.label), mech, shot, src, k, kFit, rms, flags, warnings, any};
  }

  /** A diopter's true click from groups at a few click settings at one distance:
      ladder [{clicks, poi_cm}], dist_m → {milPerClick, rms (cm), n} or null (needs 2 different clicks). */
  function clickCal(ladder, dist_m) {
    const pts = (ladder || []).filter(p => has(p.clicks) && has(p.poi_cm)).map(p => [+p.clicks, +p.poi_cm]);
    if (new Set(pts.map(p => p[0])).size < 2) return null;
    const dist = has(dist_m) && +dist_m > 0 ? +dist_m : 100;
    const mx = pts.reduce((a, p) => a + p[0], 0) / pts.length, my = pts.reduce((a, p) => a + p[1], 0) / pts.length;
    let sxy = 0, sxx = 0;
    for (const [x, y] of pts) { sxy += (x - mx) * (y - my); sxx += (x - mx) * (x - mx); }
    const slope = sxy / sxx;                                 // cm per click at dist
    const rms = Math.sqrt(pts.reduce((a, [x, y]) => a + (y - (my + slope * (x - mx))) ** 2, 0) / pts.length);
    return {milPerClick: slope / (dist / 10), rms, n: pts.length};
  }

  return {leafCal, clickCal, FLAG_MIL};
});
