/* Truing: fit a profile's muzzle-velocity scale and drag scale to dope confirmed at the range.
   Pure functions, no DOM: loaded as a plain script (window.BallisticsTruing, after solver.js) or with require().

   Each observation is one range where the group landed on the aim point, with the elevation that did it (from the
   zero, in mrad) and that day's conditions already folded into its solver input. The fit is the usual practice:
   - velocity from shots still well supersonic (Mach ≥ 1.2 at the target), where drop depends mostly on velocity;
   - drag scale from the shots further out (below Mach 1.2), where the drag model is least certain;
   with both, the two fits alternate until they settle (the drag scale also moves the supersonic part a little).
   One unknown per regime: shots all in one regime can only true that one. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory(require("./solver.js"));
  else root.BallisticsTruing = factory(root.Ballistics);
})(this, function (B) {
  "use strict";

  const LIMITS = {mvScale: [0.8, 1.2], dragScale: [0.5, 1.5]};     // the profile's validation limits
  const SPLIT_MACH = 1.2;
  const clamp = (v, [lo, hi]) => Math.min(hi, Math.max(lo, v));

  /** The solver's row for one observation with these scales (null when it doesn't reach). */
  function predict(o, sc) {
    let r;
    try { r = B.solve(Object.assign({}, o.input, sc, {mainOnly: true}), [o.range]).rows[0]; } catch (e) { return null; }
    return r && r.reached ? r : null;
  }

  /** Least squares on one scale (Gauss–Newton, numeric derivative); residuals in mrad. */
  function fitOne(obs, key, sc) {
    const h = key === "mvScale" ? 0.002 : 0.01;
    let s = sc[key];
    for (let it = 0; it < 12; it++) {
      let num = 0, den = 0;
      for (const o of obs) {
        const a = predict(o, Object.assign({}, sc, {[key]: s})), b = predict(o, Object.assign({}, sc, {[key]: s + h}));
        if (!a || !b) continue;
        const d = (b.elevMrad - a.elevMrad) / h;
        num += d * (a.elevMrad - o.elevMrad); den += d * d;
      }
      if (!(den > 1e-12)) break;
      const next = clamp(s - num / den, LIMITS[key]);
      const done = Math.abs(next - s) < 1e-5;
      s = next;
      if (done) break;
    }
    return s;
  }

  const rms = xs => xs.length ? Math.sqrt(xs.reduce((a, x) => a + x * x, 0) / xs.length) : null;

  /** obs: [{input, range, elevMrad, id?}]; mode: "auto" | "velocity" | "drag" | "both";
      start: the profile's current {mvScale, dragScale}.
      → {mvScale, dragScale, mode (what was fitted), rows: [{id, range, observed, before, after, mach}],
         rmsBefore, rmsAfter, warnings: [codes]} */
  function fit(obs, mode, start) {
    start = {mvScale: (start && start.mvScale) || 1, dragScale: (start && start.dragScale) || 1};
    const warnings = [];
    const usable = [], before = new Map();
    for (const o of obs) {
      const r = predict(o, start);
      if (r) { usable.push(o); before.set(o, r); } else warnings.push("not_reached");
    }
    if (!usable.length) return {mvScale: start.mvScale, dragScale: start.dragScale, mode: "none", rows: [],
                                rmsBefore: null, rmsAfter: null, warnings: warnings.concat("no_observations")};
    const fast = usable.filter(o => before.get(o).mach >= SPLIT_MACH), slow = usable.filter(o => !fast.includes(o));
    let m = mode || "auto";
    if (m === "auto") m = fast.length && slow.length ? "both" : fast.length ? "velocity" : "drag";
    if (m === "both" && !(fast.length && slow.length)) {
      warnings.push(fast.length ? "both_needs_slow" : "both_needs_fast");
      m = fast.length ? "velocity" : "drag";
    }
    if (m === "velocity" && !fast.length) warnings.push("velocity_from_slow");   // works, but drag error leaks in
    if (Math.max(...usable.map(o => o.range)) < 300) warnings.push("short_range");

    const sc = Object.assign({}, start);
    if (m === "velocity") sc.mvScale = fitOne(usable, "mvScale", sc);
    else if (m === "drag") sc.dragScale = fitOne(usable, "dragScale", sc);
    else {
      for (let i = 0; i < 6; i++) {
        const mv0 = sc.mvScale, dr0 = sc.dragScale;
        sc.mvScale = fitOne(fast, "mvScale", sc);
        sc.dragScale = fitOne(slow, "dragScale", sc);
        if (Math.abs(sc.mvScale - mv0) < 1e-5 && Math.abs(sc.dragScale - dr0) < 1e-5) break;
      }
    }
    for (const k of ["mvScale", "dragScale"]) if (sc[k] <= LIMITS[k][0] + 1e-9 || sc[k] >= LIMITS[k][1] - 1e-9) warnings.push("at_limit");

    const rows = obs.map(o => {
      const b = before.get(o), a = b ? predict(o, sc) : null;
      return {id: o.id, range: o.range, observed: o.elevMrad, before: b ? b.elevMrad : null,
              after: a ? a.elevMrad : null, mach: b ? b.mach : null};
    });
    const ok = rows.filter(r => r.before != null && r.after != null);
    return {mvScale: sc.mvScale, dragScale: sc.dragScale, mode: m, rows,
            rmsBefore: rms(ok.map(r => r.before - r.observed)), rmsAfter: rms(ok.map(r => r.after - r.observed)),
            warnings: [...new Set(warnings)]};
  }

  return {fit, predict, LIMITS, SPLIT_MACH};
});
