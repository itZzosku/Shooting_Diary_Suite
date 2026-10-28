/* Reticles: compile a builder spec (written in the terms of a maker's subtension drawing) into drawing
   primitives, and draw primitives + hold points as SVG. Pure functions; window.Reticle or require() in Node.

   Coordinates are in the reticle's own unit (MRAD or MOA) at its design magnification. Origin at the centre,
   x to the right, y DOWN: a positive y is below the centre, where holdover marks are.

   Spec (all lengths in the reticle's unit):
   { v: 1, unit: "MRAD"|"MOA", focal_plane: "FFP"|"SFP", design_mag, field: half-width drawn,
     centre: {type: "dot"|"cross"|"open"|"none", r, size, w, gap},
     stadia: {w, mirror: "lr"|"", up|down|left|right: {to, segments: [[from, to], ...] (thin line pieces; default
              one piece from the centre gap to the post), post: {from, w, taper (length of the pointed tip),
              bar: {at, len, w} (a bar across the post)}}},
     hashes: [{arms: ["left","right","up","down"], from, to, step, len, w, side: "both"|"out"|"in",
               major: {every, len}, labels: {every, size, offset, place: "after"|"before"}}],
              (labels "after" = below a horizontal arm / right of a vertical one, "before" = above / left)
     tree: {rows: {from, to, step}, half_width: {kind: "constant"|"linear"|"list", value, at_from, at_to, values},
            inner: no marks closer than this to the vertical line,
            marks: {kind: "dot"|"hash"|"cross", step, r, len, w, major: {every, r}},
            labels: {every, size, x (fixed position, that mark is left out), texts: {"10": "X"}}},
     extras: [primitives] }
   Primitives: {t:"line",x1,y1,x2,y2,w} {t:"rect",x,y,width,height} {t:"dot",x,y,r} {t:"circle",x,y,r,w}
               {t:"poly",points:[[x,y]...],w,closed} {t:"text",x,y,text,size,anchor} */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.Reticle = factory();
})(this, function () {
  "use strict";
  const MAX = 2000;
  const DIRS = {right: [1, 0], left: [-1, 0], up: [0, -1], down: [0, 1]};
  const r4 = v => Math.round(v * 1e4) / 1e4;
  const label = v => String(+v.toFixed(3));

  function steps(from, to, step) {
    const out = [];
    if (!(step > 0) || to < from) return out;
    const n = Math.floor((to - from) / step + 1e-6);
    for (let i = 0; i <= n && out.length < MAX; i++) out.push(r4(from + i * step));
    return out;
  }

  /** spec → {elements, warnings} */
  function compile(spec) {
    const el = [], warn = [];
    const push = e => { if (el.length < MAX) el.push(e); };
    const s = spec || {};
    const field = s.field > 0 ? s.field : 10;
    const st = s.stadia || {};
    const arm = name => {
      if (s.stadia && s.stadia.mirror === "lr" && name === "left") return st.right;
      return st[name];
    };
    const c = s.centre || {type: "none"};
    const gap = c.gap > 0 ? c.gap : 0;
    // main stadia (with thick outer posts)
    for (const name of ["up", "down", "left", "right"]) {
      const a = arm(name);
      if (!a || !(a.to > 0)) continue;
      const [dx, dy] = DIRS[name], w = st.w > 0 ? st.w : 0.03;
      const thinEnd = a.post && a.post.from > gap && a.post.from < a.to ? a.post.from : a.to;
      const pieces = Array.isArray(a.segments) && a.segments.length ? a.segments : [[gap, thinEnd]];
      for (const [s0, s1] of pieces) {
        if (!(s1 > s0)) continue;
        push({t: "line", x1: r4(dx * s0), y1: r4(dy * s0), x2: r4(dx * s1), y2: r4(dy * s1), w});
      }
      if (thinEnd < a.to) {
        const P = a.post, pw = P.w > 0 ? P.w : w * 5, tap = P.taper > 0 ? Math.min(P.taper, a.to - thinEnd) : 0;
        // the post as a polygon along the arm: pointed tip (stadia width) widening to pw over `tap`, then straight
        const along = [[thinEnd, w / 2], [thinEnd + tap, pw / 2], [a.to, pw / 2]];
        const pts = along.concat(along.slice().reverse().map(([u, v]) => [u, -v])).map(([u, v]) => dx ? [r4(dx * u), r4(v)] : [r4(v), r4(dy * u)]);
        push({t: "poly", points: pts, w: 0, closed: true});
        if (P.bar && P.bar.at > 0 && P.bar.len > 0) {
          const bw = P.bar.w > 0 ? P.bar.w : pw / 2, at = P.bar.at;
          if (dx) push({t: "rect", x: r4(dx * at - bw / 2), y: r4(-P.bar.len / 2), width: r4(bw), height: r4(P.bar.len)});
          else push({t: "rect", x: r4(-P.bar.len / 2), y: r4(dy * at - bw / 2), width: r4(P.bar.len), height: r4(bw)});
        }
      }
    }
    // centre
    if (c.type === "dot") push({t: "dot", x: 0, y: 0, r: c.r > 0 ? c.r : 0.05});
    if (c.type === "open") push({t: "circle", x: 0, y: 0, r: c.r > 0 ? c.r : 0.25, w: c.w > 0 ? c.w : 0.02});
    if (c.type === "cross" && gap > 0) {
      const z = c.size > 0 ? c.size : gap, w = c.w > 0 ? c.w : (st.w || 0.03);
      push({t: "line", x1: -z, y1: 0, x2: z, y2: 0, w}); push({t: "line", x1: 0, y1: -z, x2: 0, y2: z, w});
    }
    // hash sets
    for (const h of s.hashes || []) {
      if (!(h.step > 0)) { warn.push("A hash set needs a step."); continue; }
      const majEvery = h.major && h.major.every > 0 ? Math.round(h.major.every) : 0;
      const labEvery = h.labels && h.labels.every > 0 ? Math.round(h.labels.every) : 0;
      for (const name of h.arms || []) {
        const a = arm(name), [dx, dy] = DIRS[name] || [0, 0];
        if (!dx && !dy) continue;
        if (a && a.to > 0 && h.to > a.to + 1e-9) warn.push(`Hashes on the ${name} arm go past its end (${a.to}).`);
        for (const d of steps(h.from > 0 ? h.from : h.step, h.to, h.step)) {
          const k = Math.round(d / h.step), major = majEvery && k % majEvery === 0;
          const len = major && h.major.len > 0 ? h.major.len : (h.len > 0 ? h.len : 0.1);
          const w = h.w > 0 ? h.w : (st.w || 0.03);
          const side = h.side || "both";
          let a0 = -len / 2, a1 = len / 2;
          if (side === "out") { a0 = 0; a1 = len; }               // one side: right of up/down arms, below left/right arms
          if (side === "in") { a0 = -len; a1 = 0; }
          const px = dx * d, py = dy * d;
          if (dx) push({t: "line", x1: r4(px), y1: r4(a0), x2: r4(px), y2: r4(a1), w});
          else push({t: "line", x1: r4(a0), y1: r4(py), x2: r4(a1), y2: r4(py), w});
          if (labEvery && k % labEvery === 0) {
            const off = h.labels.offset > 0 ? h.labels.offset : len / 2 + 0.25, size = h.labels.size > 0 ? h.labels.size : 0.3;
            const before = h.labels.place === "before";
            if (dx) push({t: "text", x: r4(px), y: r4(before ? Math.min(a0, 0) - off : Math.max(a1, 0) + off + size * 0.7), text: label(d), size, anchor: "middle"});
            else push({t: "text", x: r4(before ? Math.min(a0, 0) - off : Math.max(a1, 0) + off), y: r4(py + size * 0.35), text: label(d), size, anchor: before ? "end" : "start"});
          }
        }
      }
    }
    // holdover tree
    const tr = s.tree;
    if (tr && tr.rows && tr.rows.step > 0) {
      const hw = tr.half_width || {kind: "constant", value: 1};
      const ys = steps(tr.rows.from, tr.rows.to, tr.rows.step);
      const m = tr.marks || {kind: "dot", step: 0.5};
      const mstep = m.step > 0 ? m.step : 0.5;
      ys.forEach((y, i) => {
        let w;
        if (hw.kind === "list") w = (hw.values || [])[i];
        else if (hw.kind === "linear") {
          const span = ys[ys.length - 1] - ys[0];
          w = span > 0 ? hw.at_from + (hw.at_to - hw.at_from) * (y - ys[0]) / span : hw.at_from;
        } else w = hw.value;
        if (!(w > 0)) return;
        if (w > field) warn.push(`Tree row at ${y} is wider than the drawn field.`);
        const n = Math.floor(w / mstep + 1e-6);
        const inner = tr.inner > 0 ? tr.inner : 0, labX = tr.labels && tr.labels.x != null ? +tr.labels.x : null;
        for (let k = -n; k <= n; k++) {
          if (k === 0 && arm("down") && arm("down").to >= y) continue;   // the stadia is already there
          if (Math.abs(k * mstep) < inner - 1e-6) continue;
          if (labX != null && Math.abs(k * mstep - labX) < 1e-6) continue;  // the row's number sits here
          const x = r4(k * mstep);
          if (m.kind === "hash") push({t: "line", x1: x, y1: r4(y - (m.len || 0.1) / 2), x2: x, y2: r4(y + (m.len || 0.1) / 2), w: m.w || 0.02});
          else if (m.kind === "cross") {
            const z = (m.len || 0.1) / 2, ww = m.w || 0.02;
            push({t: "line", x1: r4(x - z), y1: y, x2: r4(x + z), y2: y, w: ww}); push({t: "line", x1: x, y1: r4(y - z), x2: x, y2: r4(y + z), w: ww});
          } else {
            const big = m.major && m.major.every > 0 && Math.abs(x / m.major.every - Math.round(x / m.major.every)) < 1e-6;
            push({t: "dot", x, y, r: big && m.major.r > 0 ? m.major.r : (m.r > 0 ? m.r : 0.03)});
          }
        }
        if (tr.labels && tr.labels.every > 0 && Math.round((y - ys[0]) / tr.rows.step) % Math.round(tr.labels.every) === 0) {
          const size = tr.labels.size > 0 ? tr.labels.size : 0.3, text = (tr.labels.texts || {})[label(y)] || label(y);
          if (labX != null) push({t: "text", x: r4(labX), y: r4(y + size * 0.35), text, size, anchor: "middle"});
          else push({t: "text", x: r4(-n * mstep - 0.3), y: r4(y + size * 0.35), text, size, anchor: "end"});
        }
      });
    }
    for (const e of s.extras || []) if (e && e.t) push(e);
    if (el.length >= MAX) warn.push(`Too many marks: stopped at ${MAX}.`);
    if (s.focal_plane === "SFP" && !(s.design_mag > 0)) warn.push("A second focal plane reticle needs its design magnification.");
    return {elements: el, warnings: Array.from(new Set(warn))};
  }

  /** True angular size factor of the reticle at magnification `mag`: SFP marks shrink as power goes up. */
  function sfpFactor(ret, mag) {
    if (!ret || ret.focal_plane !== "SFP" || !(ret.design_mag > 0) || !(mag > 0)) return 1;
    return ret.design_mag / mag;
  }

  /** Convert an angle between units (mrad ↔ MOA). */
  function convert(v, from, to) {
    if (from === to || !from || !to) return v;
    const moaPerMrad = 10800 / Math.PI / 1000;
    return from === "MRAD" ? v * moaPerMrad : v / moaPerMrad;
  }

  const esc = s => String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));

  /** SVG markup. opts: {half (view half-width in true reticle units), cx, cy (view centre, default 0,0),
      px (one screen pixel in true units; default half/160), k (SFP factor), ink, accent, bg,
      scope: {r, fill, outside} (round field of view: fill inside, outside colour around it),
      holds: [{x, y, label, main}] in true reticle units (x right, y down),
      targets: [{x, y, parts} | {x, y, w, h}] (see below; drawn under the reticle),
      texts: [{x, y, text, color, size (screen px), anchor, weight, halo (outline colour)}], lines: [{x1, y1, x2, y2, color, width (screen px)}],
      image: {href, transform, opacity}, extra (raw svg)} */
  function svg(elements, opts) {
    const o = Object.assign({half: 10, cx: 0, cy: 0, k: 1, ink: "currentColor", accent: "#e2590f", bg: "none"}, opts || {});
    const h = o.half, k = o.k, cx = o.cx, cy = o.cy, px = o.px > 0 ? o.px : h / 160;
    const x0 = cx - h, y0 = cy - h;
    const parts = [];
    if (o.bg !== "none") parts.push(`<rect x="${x0}" y="${y0}" width="${2 * h}" height="${2 * h}" fill="${o.bg}"/>`);
    if (o.scope && o.scope.r > 0) {
      parts.push(`<rect x="${x0}" y="${y0}" width="${2 * h}" height="${2 * h}" fill="${o.scope.outside || "#000"}"/>`);
      parts.push(`<circle cx="0" cy="0" r="${o.scope.r}" fill="${o.scope.fill || "#fff"}"/>`);
    }
    if (o.image) parts.push(`<image href="${esc(o.image.href)}" transform="${esc(o.image.transform)}" opacity="${o.image.opacity ?? 0.5}" preserveAspectRatio="none"/>`);
    if (o.scope && o.scope.r > 0) parts.push(`<clipPath id="rt-clip"><circle cx="0" cy="0" r="${o.scope.r}"/></clipPath><g clip-path="url(#rt-clip)">`);
    // Targets go under the reticle (like Strelok): solid shapes the marks are read against.
    // A target: {x, y, parts: [...]} with parts offset from x, y in true units, drawn in order:
    //   {t: "poly", points, fill, stroke, width} | {t: "circle", cx, cy, r, fill, stroke, width}
    //   {t: "line", x1, y1, x2, y2, stroke, width} | {t: "text", x, y, text, size, color}
    // fill defaults to none, stroke to #333, width (screen px) to 2; text size is in true units (scales with range).
    // Without parts: a stage target, a dashed rectangle w × h in the accent colour.
    for (const t of o.targets || []) {
      if (Array.isArray(t.parts)) {
        for (const q of t.parts) {
          const st = `fill="${q.fill || "none"}" stroke="${q.stroke || "#333"}" stroke-width="${px * (q.width != null ? q.width : 2)}" stroke-linejoin="round"`;
          if (q.t === "poly") parts.push(`<polygon points="${q.points.map(pt => `${t.x + pt[0]},${t.y + pt[1]}`).join(" ")}" ${st}/>`);
          else if (q.t === "circle") parts.push(`<circle cx="${t.x + (q.cx || 0)}" cy="${t.y + (q.cy || 0)}" r="${q.r}" ${st}/>`);
          else if (q.t === "line") parts.push(`<line x1="${t.x + q.x1}" y1="${t.y + q.y1}" x2="${t.x + q.x2}" y2="${t.y + q.y2}" ${st}/>`);
          else if (q.t === "text") parts.push(`<text x="${t.x + q.x}" y="${t.y + q.y}" font-size="${q.size}" fill="${q.color || "#111"}" text-anchor="middle" dominant-baseline="central" font-family="IBM Plex Sans, sans-serif">${esc(q.text)}</text>`);
        }
      } else if (t.w > 0) {
        const hh = t.h > 0 ? t.h : t.w;
        parts.push(`<rect x="${t.x - t.w / 2}" y="${t.y - hh / 2}" width="${t.w}" height="${hh}" fill="${o.accent}" fill-opacity="0.12" stroke="${o.accent}" stroke-width="${px}" stroke-dasharray="${px * 4} ${px * 3}"/>`);
      }
    }
    parts.push(`<g transform="scale(${k})" fill="${o.ink}" stroke="${o.ink}">`);
    for (const e of elements) {
      const sw = Math.max((e.w || 0), px / k);
      if (e.t === "line") parts.push(`<line x1="${e.x1}" y1="${e.y1}" x2="${e.x2}" y2="${e.y2}" stroke-width="${sw}" stroke-linecap="butt"/>`);
      else if (e.t === "rect") parts.push(`<rect x="${e.x}" y="${e.y}" width="${e.width}" height="${e.height}" stroke="none"/>`);
      else if (e.t === "dot") parts.push(`<circle cx="${e.x}" cy="${e.y}" r="${Math.max(e.r, 0.8 * px / k)}" stroke="none"/>`);
      else if (e.t === "circle") parts.push(`<circle cx="${e.x}" cy="${e.y}" r="${e.r}" fill="none" stroke-width="${sw}"/>`);
      else if (e.t === "poly") parts.push(`<${e.closed ? "polygon" : "polyline"} points="${e.points.map(p => p.join(",")).join(" ")}" fill="${e.closed ? o.ink : "none"}" stroke-width="${e.closed ? 0 : sw}"/>`);
      else if (e.t === "text") parts.push(`<text x="${e.x}" y="${e.y}" font-size="${e.size}" text-anchor="${e.anchor || "middle"}" stroke="none" font-family="IBM Plex Mono, monospace">${esc(e.text)}</text>`);
    }
    parts.push(`</g>`);
    for (const l of o.lines || [])
      parts.push(`<line x1="${l.x1}" y1="${l.y1}" x2="${l.x2}" y2="${l.y2}" stroke="${l.color || o.accent}" stroke-width="${px * (l.width || 1)}"/>`);
    for (const t of o.texts || [])
      parts.push(`<text x="${t.x}" y="${t.y}" font-size="${px * (t.size || 12)}" fill="${t.color || o.accent}" text-anchor="${t.anchor || "start"}" font-weight="${t.weight || 500}" font-family="IBM Plex Sans, sans-serif"${t.halo ? ` stroke="${t.halo}" stroke-width="${px * 3}" stroke-linejoin="round" paint-order="stroke"` : ""}>${esc(t.text)}</text>`);
    if (o.scope && o.scope.r > 0) parts.push(`</g>`);
    for (const p of o.holds || []) {
      const lim = h * 0.94, off = Math.abs(p.x - cx) > h || Math.abs(p.y - cy) > h;
      const x = Math.max(cx - lim, Math.min(cx + lim, p.x)), y = Math.max(cy - lim, Math.min(cy + lim, p.y));
      parts.push(`<circle cx="${x}" cy="${y}" r="${px * (p.main ? 5 : 4)}" fill="${o.accent}" stroke="${o.bg === "none" ? "#fff" : o.bg}" stroke-width="${px * 1.2}"/>`);
      if (p.label) parts.push(`<text x="${x + px * 8}" y="${y - px * 6}" font-size="${px * 13}" fill="${o.accent}" font-weight="700" font-family="IBM Plex Sans, sans-serif">${esc(p.label)}${off ? " ⇢" : ""}</text>`);
    }
    if (o.extra) parts.push(o.extra);
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${x0} ${y0} ${2 * h} ${2 * h}" role="img">` + parts.join("") + `</svg>`;
  }

  /** Which range each holdover mark is good for. rows: [{range, hold}] with hold in the reticle's unit (below the
      centre +), ascending range. Returns [{value, range}]: for 0 every crossing (the near and the far zero); for any
      other mark the last crossing, where the falling bullet passes it – close to the muzzle the bullet also starts
      below the line of sight and passes the marks rising, which is not what a holdover mark is for. */
  function rangesForHolds(rows, values) {
    const out = [];
    for (const v of values) {
      const hits = [];
      for (let i = 1; i < rows.length; i++) {
        const a = rows[i - 1], b = rows[i];
        if (a.hold == null || b.hold == null) continue;
        if (((a.hold - v) * (b.hold - v) < 0 || b.hold === v) && a.hold !== b.hold)   // exact hits counted once
          hits.push({value: v, range: a.range + (v - a.hold) / (b.hold - a.hold) * (b.range - a.range)});
      }
      if (v === 0) out.push(...hits);
      else if (hits.length) out.push(hits[hits.length - 1]);
    }
    return out;
  }

  // ------------------------------------------------------------ generic templates (not copies of any product)
  const TEMPLATES = {
    "mil-hash": {name: "Mil hash (generic)", spec: {v: 1, unit: "MRAD", focal_plane: "FFP", field: 10,
      centre: {type: "dot", r: 0.04},
      stadia: {w: 0.03, mirror: "lr", right: {to: 10, post: {from: 8, w: 0.25}}, up: {to: 10, post: {from: 8, w: 0.25}}, down: {to: 10, post: {from: 8, w: 0.25}}},
      hashes: [{arms: ["left", "right", "up", "down"], from: 0.2, to: 8, step: 0.2, len: 0.1, w: 0.02,
                major: {every: 5, len: 0.3}, labels: {every: 10, size: 0.3, offset: 0.25}}]}},
    "mil-tree": {name: "Mil grid tree (generic)", spec: {v: 1, unit: "MRAD", focal_plane: "FFP", field: 10,
      centre: {type: "dot", r: 0.04},
      stadia: {w: 0.03, mirror: "lr", right: {to: 10, post: {from: 8, w: 0.25}}, up: {to: 6}, down: {to: 10}},
      hashes: [{arms: ["left", "right", "up"], from: 0.2, to: 6, step: 0.2, len: 0.1, w: 0.02, major: {every: 5, len: 0.3}, labels: {every: 10, size: 0.3}},
               {arms: ["down"], from: 0.2, to: 1, step: 0.2, len: 0.1, w: 0.02, major: {every: 5, len: 0.3}}],
      tree: {rows: {from: 1, to: 10, step: 1}, half_width: {kind: "linear", at_from: 1, at_to: 5},
             marks: {kind: "dot", step: 0.5, r: 0.035}, labels: {every: 2, size: 0.3}}}},
    "moa-hash": {name: "MOA hash (generic)", spec: {v: 1, unit: "MOA", focal_plane: "FFP", field: 30,
      centre: {type: "cross", gap: 1, size: 1, w: 0.1},
      stadia: {w: 0.1, mirror: "lr", right: {to: 30, post: {from: 24, w: 0.8}}, up: {to: 30, post: {from: 24, w: 0.8}}, down: {to: 30, post: {from: 24, w: 0.8}}},
      hashes: [{arms: ["left", "right", "up", "down"], from: 2, to: 24, step: 2, len: 0.6, w: 0.08,
                major: {every: 5, len: 1.6}, labels: {every: 5, size: 1, offset: 0.8}}]}}
  };

  return {compile, svg, sfpFactor, convert, rangesForHolds, TEMPLATES, MAX};
});
