/* Shared by the diary pages.
   1. Drafts: what is typed into a form is kept on this phone (localStorage, per account and record) until it
      is saved, so closing a sheet, an accidental Back or the browser dropping the page loses nothing.
      Reopening the same record brings the unsaved values back with a "Discard" option.
   2. Back closes the open sheet (one at a time) instead of leaving the page. */
(function () {
  "use strict";
  const user = decodeURIComponent((document.cookie.match(/(?:^|; )diary_user=([^;]*)/) || [])[1] || "");
  const page = location.pathname.replace(/\/+$/, "").split("/").pop() || "home";
  const P = `draft:${user}:${page}:`, KEEP_DAYS = 14;
  // Fields are keyed by id, or by data-k for forms registered with {dataK:true} (fixed, non-repeating fields)
  const fkey = (e, dk) => e.id || (dk && e.dataset.k ? "k:" + e.dataset.k + (e.dataset.fk ? ":" + e.dataset.fk : "") : "");
  const fieldsOf = (f, dk) => [...f.querySelectorAll("input,select,textarea")]
    .filter(e => fkey(e, dk) && !["file", "password", "hidden", "submit", "button", "reset"].includes(e.type));
  const ls = {
    get(k) { try { return JSON.parse(localStorage.getItem(k) || "null"); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* full or blocked */ } },
    del(k) { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } },
  };
  // Old drafts go after KEEP_DAYS
  try {
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const k = localStorage.key(i);
      if (k && k.startsWith("draft:")) { const d = ls.get(k); if (!d || Date.now() - d.t > KEEP_DAYS * 864e5) ls.del(k); }
    }
  } catch (e) { /* ignore */ }

  const reg = new WeakMap();
  const key = (name, ctx) => P + name + ":" + (ctx || "new");
  function read(form, dk) { const o = {}; fieldsOf(form, dk).forEach(e => { o[fkey(e, dk)] = e.type === "checkbox" || e.type === "radio" ? e.checked : e.value; }); return o; }
  function write(form, o, dk) {
    fieldsOf(form, dk).forEach(e => {
      const k = fkey(e, dk); if (!(k in o)) return;
      if (e.type === "checkbox" || e.type === "radio") e.checked = !!o[k]; else e.value = o[k];
    });
  }
  function snap(r) { return JSON.stringify({ f: read(r.form, r.dataK), x: r.extra ? r.extra() : null }); }
  function note(r, t) {
    let n = r.form.querySelector(":scope > .draftnote");
    if (!n) {
      n = document.createElement("div"); n.className = "draftnote"; n.setAttribute("role", "status");
      const bar = r.form.querySelector(":scope > .sheet-bar");
      if (bar) bar.after(n); else r.form.prepend(n);
      n.addEventListener("click", e => {
        if (!e.target.closest("[data-discard]")) return;
        Draft.done(r.form); n.hidden = true;
        if (r.reopen) r.reopen();
      });
    }
    const d = new Date(t), hm = String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
    n.innerHTML = `Unsaved changes from ${d.getDate()}/${d.getMonth() + 1} ${hm} brought back. <button type="button" data-discard>Discard</button>`;
    n.hidden = false;
  }
  function store(r) {
    if (!r.ctx) return;
    const now = snap(r);
    if (now === r.base) ls.del(key(r.name, r.ctx)); else ls.set(key(r.name, r.ctx), { t: Date.now(), v: JSON.parse(now) });
  }

  const Draft = {
    /* Register a form once. opts: extra() → state not in id'd fields (e.g. rows), apply(x) restores it,
       after() refreshes what depends on the fields, reopen() shows the saved record again after Discard. */
    form(form, name, opts = {}) {
      const r = Object.assign({ form, name, ctx: null, base: null, timer: 0 }, opts);
      reg.set(form, r);
      const later = () => { clearTimeout(r.timer); r.timer = setTimeout(() => store(r), 250); };
      form.addEventListener("input", later); form.addEventListener("change", later); form.addEventListener("click", later);
      return r;
    },
    /* The form has just been filled for record ctx (or "new"): remember it as the saved state and bring back
       a draft if there is one. Returns true when a draft was restored. */
    open(form, ctx) {
      const r = reg.get(form); if (!r) return false;
      clearTimeout(r.timer);
      r.ctx = ctx || "new"; r.base = snap(r);
      const n = form.querySelector(":scope > .draftnote"); if (n) n.hidden = true;
      const d = ls.get(key(r.name, r.ctx));
      if (!d || !d.v || JSON.stringify(d.v) === r.base) return false;
      write(form, d.v.f || {}, r.dataK);
      if (r.apply && d.v.x) r.apply(d.v.x);
      if (r.after) r.after();                     // labels/names that depend on the fields
      note(r, d.t);
      return true;
    },
    save(form) { const r = reg.get(form); if (r) { clearTimeout(r.timer); store(r); } },
    /* Saved (or deleted): drop the draft. */
    done(form) {
      const r = reg.get(form); if (!r) return;
      clearTimeout(r.timer); if (r.ctx) ls.del(key(r.name, r.ctx));
      r.base = snap(r);
      const n = form.querySelector(":scope > .draftnote"); if (n) n.hidden = true;
    },
    // Lower-level, for pages that keep a form's state in an object
    get: (name, ctx) => { const d = ls.get(key(name, ctx)); return d ? d.v : null; },
    getT: (name, ctx) => { const d = ls.get(key(name, ctx)); return d ? d.t : 0; },
    set: (name, ctx, v) => ls.set(key(name, ctx), { t: Date.now(), v }),
    clear: (name, ctx) => ls.del(key(name, ctx)),
    note: (form, t, reopen) => note({ form, reopen }, t),
  };
  window.Draft = Draft;
  const css = document.createElement("style");
  css.textContent = ".draftnote{border:1px solid var(--accent,#e2590f);border-radius:8px;padding:8px 10px;font-size:.85rem;" +
    "display:flex;gap:10px;align-items:center;justify-content:space-between;flex-wrap:wrap;margin:8px 0}" +
    ".draftnote[hidden]{display:none}.draftnote button{font:inherit;font-weight:600;border:1px solid var(--line,#ccc);" +
    "border-radius:999px;padding:5px 12px;background:transparent;color:inherit;cursor:pointer}";
  document.head.appendChild(css);

  /* ---- Back closes the topmost open sheet ---- */
  const SEL = ".sheet,.sigfull";
  const open = () => [...document.querySelectorAll(SEL)].filter(e => !e.hidden);
  const z = e => parseInt(getComputedStyle(e).zIndex, 10) || 0;
  let depth = 0, skip = 0;
  // Opened on one of our own sheet entries (reload, or Back from another page while a form was open): just
  // unmark it. Stepping back from here would leave the document and could bring back a frozen copy of the
  // page (the browser's back/forward cache) stuck at "Loading…".
  const unmark = () => { if (history.state && history.state.sheet) { try { history.replaceState(null, ""); } catch (e) { /* ignore */ } } };
  unmark();
  window.addEventListener("pageshow", e => { if (e.persisted) { depth = 0; skip = 0; unmark(); } });
  function sync() {
    const n = open().length;
    if (n > depth) { while (depth < n) { history.pushState({ sheet: ++depth }, ""); } }
    else if (n < depth) { const k = depth - n; depth = n; skip++; history.go(-k); }
  }
  let q = false;
  const mo = new MutationObserver(() => { if (!q) { q = true; setTimeout(() => { q = false; sync(); }, 0); } });
  const watch = () => document.querySelectorAll(SEL).forEach(e => mo.observe(e, { attributes: true, attributeFilter: ["hidden"] }));
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", watch); else watch();
  window.addEventListener("popstate", () => {
    if (skip) { skip--; return; }
    const o = open();
    depth = Math.max(0, depth - 1);              // the browser has already stepped back one entry
    if (!o.length) { depth = 0; return; }
    o.sort((a, b) => z(a) - z(b) || (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1));
    const top = o[o.length - 1];
    const b = top.querySelector("[data-back]") || top.querySelector(".sheet-bar button.ghost:not(.primary)");
    if (b) b.click(); else top.hidden = true;
    setTimeout(sync, 0);                         // a sheet that didn't close gets its entry back
  });
})();
