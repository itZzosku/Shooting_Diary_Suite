/* Ballistic solver: modified point-mass (3DOF) trajectory, RK4 in SI units.
   Pure functions, no DOM: loaded as a plain script (window.Ballistics, needs drag.js first) or with require()
   in Node tests. Design: "Ballistic Calculator — Design Document", Solver design.

   Frame: x along the line of sight, y up (perpendicular to it), z right. The bore starts sightHeight below the
   line of sight, elevated by the zero angle. Gravity is resolved along the inclined line of sight, so incline
   is exact. Wind is horizontal; drag acts on the air-relative velocity.

   Signs in the results: elevation hold + = up (dial up), windage hold + = right (dial / hold right).
   Wind direction is the clock face it blows FROM: 12 = head wind, 3 = from the right, 9 = from the left. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory(require("./drag.js"));
  else root.Ballistics = factory(root.BallisticsDrag);
})(this, function (DRAG) {
  "use strict";

  const G = 9.80665;                       // m/s²
  const OMEGA = 7.2921159e-5;              // Earth rotation, rad/s
  const BC_SI = 703.0696;                  // lb/in² → kg/m²
  const MOA_PER_MRAD = 10800 / Math.PI / 1000;   // 3.43775
  const STD = {tempC: 15, hpa: 1013.25, rh: 0};
  const DT = 0.0005;                       // s, RK4 step
  const MAX_TIME = 10;                     // s
  const GRAIN = 6.479891e-5;               // kg

  // ------------------------------------------------------------ drag tables (monotone cubic in Mach)
  const curves = {};
  function prepare(points) {
    const n = points.length, x = points.map(p => p[0]), y = points.map(p => p[1]);
    const h = [], d = [], m = new Array(n);
    for (let i = 0; i < n - 1; i++) { h.push(x[i + 1] - x[i]); d.push((y[i + 1] - y[i]) / h[i]); }
    m[0] = d[0]; m[n - 1] = d[n - 2];
    for (let i = 1; i < n - 1; i++) {            // Fritsch–Carlson (PCHIP)
      if (d[i - 1] * d[i] <= 0) m[i] = 0;
      else { const w1 = 2 * h[i] + h[i - 1], w2 = h[i] + 2 * h[i - 1]; m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i]); }
    }
    return {x, y, m};
  }
  function curve(name, points) {
    if (points) return prepare(points);
    if (!curves[name]) {
      if (!DRAG[name]) throw new Error("unknown drag model " + name);
      curves[name] = prepare(DRAG[name]);
    }
    return curves[name];
  }
  function cdAt(c, mach) {
    const x = c.x, n = x.length;
    if (mach <= x[0]) return c.y[0];
    if (mach >= x[n - 1]) return c.y[n - 1];
    let lo = 0, hi = n - 1;
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (x[mid] > mach) hi = mid; else lo = mid; }
    const h = x[hi] - x[lo], t = (mach - x[lo]) / h, t2 = t * t, t3 = t2 * t;
    return (2 * t3 - 3 * t2 + 1) * c.y[lo] + (t3 - 2 * t2 + t) * h * c.m[lo]
         + (-2 * t3 + 3 * t2) * c.y[hi] + (t3 - t2) * h * c.m[hi];
  }

  // ------------------------------------------------------------ atmosphere
  function satVapourHpa(tempC) {          // Buck (1996), over water
    return 6.1121 * Math.exp((18.678 - tempC / 234.5) * (tempC / (257.14 + tempC)));
  }
  /** {tempC, hpa (station pressure), rh %} → {rho kg/m³, c m/s} */
  function atmosphere(a) {
    const t = a.tempC != null ? +a.tempC : STD.tempC, p = (a.hpa != null ? +a.hpa : STD.hpa) * 100;
    const rh = Math.min(100, Math.max(0, a.rh != null ? +a.rh : 0)) / 100;
    const T = t + 273.15, pv = rh * satVapourHpa(t) * 100, pd = p - pv;
    return {rho: pd / (287.058 * T) + pv / (461.495 * T), c: 331.3 * Math.sqrt(1 + t / 273.15)};
  }
  /** Sea-level pressure (QNH, as weather services report) → station pressure at altM. */
  function stationFromSeaLevel(hpaSea, altM, tempC) {
    const T = (tempC != null ? tempC : 15) + 273.15 + 0.0065 * altM / 2;   // mean column temperature
    return hpaSea * Math.exp(-G * (altM || 0) / (287.058 * T));
  }

  // ------------------------------------------------------------ bullet
  function bcAt(bullet, speed) {
    const bands = bullet.bands;              // [[v_min m/s, bc], ...] – the band with the highest v_min ≤ speed
    if (bands && bands.length) {
      let bc = bands[0][1];
      for (const [v, b] of bands) if (speed >= v) bc = b;
      return bc;
    }
    return bullet.bc;
  }
  /** Miller stability factor at the muzzle, corrected for velocity and air density. null when data is missing. */
  function stability(bullet, twistMm, mv, atmo) {
    const d = bullet.diameterMm / 25.4, len = bullet.lengthMm / 25.4, w = bullet.weightGr;
    if (!(d > 0 && len > 0 && w > 0 && twistMm > 0)) return null;
    const t = (twistMm / 25.4) / d, l = len / d;
    let sg = 30 * w / (t * t * Math.pow(d, 3) * l * (1 + l * l));
    sg *= Math.cbrt((mv / 0.3048) / 2800);
    const a = atmo || STD, tF = (a.tempC != null ? a.tempC : 15) * 9 / 5 + 32, inHg = (a.hpa != null ? a.hpa : 1013.25) / 33.8639;
    return sg * ((tF + 460) / (59 + 460)) * (29.92 / inHg);
  }

  // ------------------------------------------------------------ trajectory
  function windVector(w, angleRad) {
    if (!w || !w.ms) return [0, 0, 0];
    const a = (+w.clock || 0) * Math.PI / 6;            // from 12 o'clock, clockwise
    const hx = -w.ms * Math.cos(a), hz = -w.ms * Math.sin(a);   // horizontal wind, x downrange
    return [hx * Math.cos(angleRad), -hx * Math.sin(angleRad), hz];
  }
  function omegaVector(latDeg, azDeg, angleRad) {
    const lat = latDeg * Math.PI / 180, az = (azDeg || 0) * Math.PI / 180;
    const hx = OMEGA * Math.cos(lat) * Math.cos(az), hy = OMEGA * Math.sin(lat), hz = -OMEGA * Math.cos(lat) * Math.sin(az);
    const c = Math.cos(angleRad), s = Math.sin(angleRad);
    return [c * hx + s * hy, -s * hx + c * hy, hz];
  }

  /** Integrate one trajectory. p: {mv, theta (rad), sightH (m), angle (rad), rho, c, bullet, dragCurve, dragScale,
      wind [x,y,z], omega [x,y,z] | null}. Calls sample(x) targets in ascending order; returns rows. */
  function fly(p, ranges) {
    const out = [];
    const gx = -G * Math.sin(p.angle), gy = -G * Math.cos(p.angle);
    const b = p.bullet, cur = p.dragCurve, k0 = Math.PI * p.rho / 8;
    const custom = b.model === "custom";
    const mass = (b.weightGr || 0) * GRAIN, area = Math.PI * Math.pow((b.diameterMm || 0) / 1000, 2) / 4;
    const [wx, wy, wz] = p.wind, om = p.omega;
    function acc(vx, vy, vz, o) {
      const ux = vx - wx, uy = vy - wy, uz = vz - wz, u = Math.sqrt(ux * ux + uy * uy + uz * uz);
      const cd = cdAt(cur, u / p.c) * (p.dragScale || 1);
      const k = custom ? (0.5 * p.rho * cd * area / mass) * u : (k0 * cd / (bcAt(b, u) * BC_SI)) * u;
      let ax = -k * ux + gx, ay = -k * uy + gy, az = -k * uz;
      if (om) {                                         // −2 Ω × v
        ax -= 2 * (om[1] * vz - om[2] * vy); ay -= 2 * (om[2] * vx - om[0] * vz); az -= 2 * (om[0] * vy - om[1] * vx);
      }
      o[0] = ax; o[1] = ay; o[2] = az;
    }
    let x = 0, y = -p.sightH, z = 0, t = 0;
    let vx = p.mv * Math.cos(p.theta), vy = p.mv * Math.sin(p.theta), vz = 0;
    const k1 = [0, 0, 0], k2 = [0, 0, 0], k3 = [0, 0, 0], k4 = [0, 0, 0];
    let i = 0;
    while (i < ranges.length && ranges[i] <= 0) { out.push({x: 0, y, z, t: 0, v: p.mv}); i++; }
    while (i < ranges.length && t < MAX_TIME) {
      const h = DT;
      acc(vx, vy, vz, k1);
      acc(vx + k1[0] * h / 2, vy + k1[1] * h / 2, vz + k1[2] * h / 2, k2);
      acc(vx + k2[0] * h / 2, vy + k2[1] * h / 2, vz + k2[2] * h / 2, k3);
      acc(vx + k3[0] * h, vy + k3[1] * h, vz + k3[2] * h, k4);
      const nvx = vx + h / 6 * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0]);
      const nvy = vy + h / 6 * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1]);
      const nvz = vz + h / 6 * (k1[2] + 2 * k2[2] + 2 * k3[2] + k4[2]);
      // positions: RK4 on dx/dt = v (velocity stages are the integrals of the k's)
      const nx = x + h / 6 * (vx + 2 * (vx + k1[0] * h / 2) + 2 * (vx + k2[0] * h / 2) + (vx + k3[0] * h));
      const ny = y + h / 6 * (vy + 2 * (vy + k1[1] * h / 2) + 2 * (vy + k2[1] * h / 2) + (vy + k3[1] * h));
      const nz = z + h / 6 * (vz + 2 * (vz + k1[2] * h / 2) + 2 * (vz + k2[2] * h / 2) + (vz + k3[2] * h));
      if (nvx <= 0) break;                               // stopped going downrange
      while (i < ranges.length && ranges[i] <= nx) {     // cubic Hermite between the two steps
        const r = ranges[i];
        let s = (r - x) / (nx - x);                      // first guess, then two Newton steps on x(s) = r
        for (let n = 0; n < 2; n++) {
          const xs = herm(x, nx, vx * h, nvx * h, s), dxs = hermD(x, nx, vx * h, nvx * h, s);
          if (dxs > 0) s -= (xs - r) / dxs;
        }
        s = Math.min(1, Math.max(0, s));
        const ry = herm(y, ny, vy * h, nvy * h, s), rz = herm(z, nz, vz * h, nvz * h, s);
        const rvx = vx + (nvx - vx) * s, rvy = vy + (nvy - vy) * s, rvz = vz + (nvz - vz) * s;
        out.push({x: r, y: ry, z: rz, t: t + s * h, v: Math.sqrt(rvx * rvx + rvy * rvy + rvz * rvz)});
        i++;
      }
      x = nx; y = ny; z = nz; vx = nvx; vy = nvy; vz = nvz; t += h;
      if (y < -1000) break;
    }
    return out;
  }
  function herm(p0, p1, m0, m1, s) {
    const s2 = s * s, s3 = s2 * s;
    return (2 * s3 - 3 * s2 + 1) * p0 + (s3 - 2 * s2 + s) * m0 + (-2 * s3 + 3 * s2) * p1 + (s3 - s2) * m1;
  }
  function hermD(p0, p1, m0, m1, s) {
    const s2 = s * s;
    return (6 * s2 - 6 * s) * p0 + (3 * s2 - 4 * s + 1) * m0 + (-6 * s2 + 6 * s) * p1 + (3 * s2 - 2 * s) * m1;
  }

  /** Muzzle velocity at a powder temperature, from a reference velocity and a slope in m/s per °C. */
  function mvAt(inp, powderC) {
    const ref = +inp.mv, refT = inp.mvTempC, s = inp.tempSens;
    const v = (s && refT != null && powderC != null) ? ref + s * (powderC - refT) : ref;
    return v * (inp.mvScale || 1);
  }

  /** Bore angle (rad above the line of sight) that puts the bullet on the line of sight at the zero range,
      in the zero atmosphere, with no wind, Coriolis or spin drift. */
  function zeroAngle(inp) {
    const za = Object.assign({}, STD, inp.zeroAtmo || {});
    const atm = atmosphere(za);
    const base = {mv: mvAt(inp, za.powderC != null ? za.powderC : za.tempC), sightH: inp.sightHeightMm / 1000, angle: 0,
                  rho: atm.rho, c: atm.c, bullet: inp.bullet, dragCurve: dragCurve(inp.bullet),
                  dragScale: inp.dragScale, wind: [0, 0, 0], omega: null};
    const R = +inp.zeroRangeM;
    const f = th => { const r = fly(Object.assign({}, base, {theta: th}), [R]); return r.length ? r[0].y : -1e9; };
    let a = 0, fa = f(a), b = 0.002, fb = f(b);
    for (let n = 0; n < 40 && Math.abs(fb) > 1e-9; n++) {
      if (fb === fa) break;
      let c = b - fb * (b - a) / (fb - fa);
      if (!isFinite(c) || c < -0.1 || c > 0.3) c = (a + b) / 2;
      a = b; fa = fb; b = c; fb = f(b);
    }
    if (Math.abs(fb) > 1e-6) throw new Error("zero_not_found");
    return b;
  }
  function dragCurve(b) {
    return b.model === "custom" ? curve(null, b.curve) : curve(b.model || "G7");
  }

  /** Full solution at ranges (m, ascending). inp: {bullet:{model, bc, bands?, curve?, diameterMm, lengthMm, weightGr},
      mv, mvTempC?, tempSens?, mvScale?, dragScale?, sightHeightMm, zeroRangeM, zeroAtmo?, atmo?, powderC?,
      wind?:{ms, clock}, angleDeg?, twistMm?, twistDir?, latitude?, azimuthDeg?,
      spinDrift?, coriolis?, aeroJump? (booleans, default false), mainOnly? (skip the calm and 1 m/s crosswind
      trajectories: no windPer1Mrad / calmElevMrad, a third of the work – for truing)}. */
  function solve(inp, ranges) {
    const rs = ranges.slice().sort((a, b) => a - b);
    const atmoIn = Object.assign({}, STD, inp.atmo || {});
    const atm = atmosphere(atmoIn);
    const powder = inp.powderC != null ? inp.powderC : atmoIn.tempC;
    const mv = mvAt(inp, powder);
    const theta = zeroAngle(inp);
    const angle = (inp.angleDeg || 0) * Math.PI / 180;
    const common = {mv, theta, sightH: inp.sightHeightMm / 1000, angle, rho: atm.rho, c: atm.c,
                    bullet: inp.bullet, dragCurve: dragCurve(inp.bullet), dragScale: inp.dragScale};
    const om = inp.coriolis && inp.latitude != null ? omegaVector(+inp.latitude, +inp.azimuthDeg || 0, angle) : null;
    const main = fly(Object.assign({}, common, {wind: windVector(inp.wind, angle), omega: om}), rs);
    const extra = !inp.mainOnly;           // truing needs only the main trajectory
    const calm = extra ? fly(Object.assign({}, common, {wind: [0, 0, 0], omega: null}), rs) : [];
    const cross = extra ? fly(Object.assign({}, common, {wind: windVector({ms: 1, clock: 9}, angle), omega: null}), rs) : [];
    const twistSign = inp.twistDir === "L" ? -1 : 1;
    const sg = stability(inp.bullet, +inp.twistMm, mv, atmoIn);
    // Aerodynamic jump (Litz): MOA per mph of crosswind; right twist + wind from the left → up.
    let jumpRad = 0;
    if (inp.aeroJump && sg && inp.wind && inp.wind.ms) {
      const l = inp.bullet.lengthMm / inp.bullet.diameterMm;
      const cw = -inp.wind.ms * Math.sin((+inp.wind.clock || 0) * Math.PI / 6);   // + = blowing to the right
      jumpRad = twistSign * (0.01 * sg - 0.0024 * l + 0.032) * (cw / 0.44704) / MOA_PER_MRAD / 1000;
    }
    const rows = [];
    for (let i = 0; i < rs.length; i++) {
      const r = main[i];
      if (!r) { rows.push({range: rs[i], reached: false}); continue; }
      let z = r.z;
      if (inp.spinDrift && sg) z += twistSign * 1.25 * (sg + 1.2) * Math.pow(r.t, 1.83) * 0.0254;
      const x = r.x || 1e-9;
      const y = r.y + jumpRad * x;
      const mach = r.v / atm.c;
      rows.push({
        range: rs[i], reached: true,
        dropM: y, driftM: z,
        elevMrad: r.x > 0 ? Math.atan2(-y, x) * 1000 : 0,
        windMrad: r.x > 0 ? Math.atan2(-z, x) * 1000 : 0,
        windPer1Mrad: cross[i] && r.x > 0 ? Math.abs(Math.atan2(cross[i].z, x) * 1000) : null,
        calmElevMrad: calm[i] && r.x > 0 ? Math.atan2(-calm[i].y, x) * 1000 : null,
        v: r.v, mach, tof: r.t,
        energyJ: inp.bullet.weightGr ? 0.5 * inp.bullet.weightGr * GRAIN * r.v * r.v : null,
        transonic: mach < 1.2 && mach >= 1.0, subsonic: mach < 1.0
      });
    }
    return {rows, mv, theta, sg, atm, muzzleMach: mv / atm.c};
  }

  // ------------------------------------------------------------ units and turrets
  function toUnit(mrad, unit) { return unit === "MOA" ? mrad * MOA_PER_MRAD : mrad; }
  /** A hold in the scope's unit → clicks, rounded value and turret turns. */
  function dial(hold, scope) {
    const click = scope && scope.click > 0 ? scope.click : 0.1;
    const clicks = Math.round(hold / click);
    const value = clicks * click;
    const out = {clicks, value};
    const turn = scope && scope.turn;
    if (turn > 0) {
      const abs = Math.abs(value), n = Math.floor(abs / turn + 1e-9);
      out.turns = n; out.rest = Math.round((abs - n * turn) / click) * click;
    }
    return out;
  }

  return {G, MOA_PER_MRAD, STD, atmosphere, stationFromSeaLevel, stability, mvAt, zeroAngle, solve, toUnit, dial,
          cdAt: (model, mach) => cdAt(curve(model), mach)};
});
