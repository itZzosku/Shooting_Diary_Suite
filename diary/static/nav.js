/* The top bar (.pnav): Home and the three features this account picked (home page → Top bar).
   Loaded right after the bar so it is drawn before the page paints. The choice is kept with the account
   (api/nav) and on this phone (localStorage) so it shows at once and offline. */
(function () {
  "use strict";
  const FEATURES = [
    {id: "log", label: "Diary", name: "Diary"},
    {id: "velocity", label: "Velocity", name: "Velocity"},
    {id: "zeroes", label: "Zeroes", name: "Zeroes"},
    {id: "ballistics", label: "Ballistics", name: "Ballistic calculator"},
    {id: "loans", label: "Loans", name: "Guns loaned"},
    {id: "calculator", label: "Clicks", name: "Click calculator"},
    {id: "ammo", label: "Ammo", name: "Club ammo", href: "/ammo/"},
  ];
  const DEFAULT = ["log", "velocity", "zeroes"];
  const user = decodeURIComponent((document.cookie.match(/(?:^|; )diary_user=([^;]*)/) || [])[1] || "");
  const KEY = "nav:" + user;
  const valid = l => Array.isArray(l) && l.length === 3 && new Set(l).size === 3 && l.every(x => FEATURES.some(f => f.id === x));
  const stored = () => { try { const l = JSON.parse(localStorage.getItem(KEY) || "null"); return valid(l) ? l : null; } catch (e) { return null; } };
  const keep = l => { try { localStorage.setItem(KEY, JSON.stringify(l)); } catch (e) { /* full or blocked */ } };
  const here = location.pathname.replace(/\/+$/, "").split("/").pop();
  const esc = s => String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
  // relative to /diary/ (the home page is /diary/ itself, the others /diary/<page>)
  const base = location.pathname.endsWith("/") ? "" : "./";

  function draw(list) {
    document.querySelectorAll(".pnav").forEach(nav => {
      const home = nav.querySelector("a");
      nav.querySelectorAll("a:not(:first-child)").forEach(a => a.remove());
      list.forEach(id => {
        const f = FEATURES.find(x => x.id === id);
        nav.insertAdjacentHTML("beforeend", `<a href="${esc(f.href || base + f.id)}"${!f.href && here === f.id ? ' aria-current="page"' : ""}>${esc(f.label)}</a>`);
      });
      if (home) home.toggleAttribute("aria-current", !here || here === "diary");
    });
  }
  let cur = stored() || DEFAULT;
  draw(cur);
  // the account's choice (another device may have changed it)
  fetch((location.pathname.endsWith("/") ? "" : "./") + "api/nav", {credentials: "same-origin", headers: {"X-Requested-With": "fetch"}})
    .then(r => r.ok ? r.json() : null).then(j => {
      if (j && valid(j.nav) && JSON.stringify(j.nav) !== JSON.stringify(cur)) { cur = j.nav; keep(cur); draw(cur); }
    }).catch(() => {});

  /* Every sheet gets its main button (Save, Done, Log …) in the top bar too, next to Close, so a long form can be
     saved without scrolling to the bottom. It presses the sheet's first visible primary/submit button and takes
     its name (shortened); a sheet with none, or with a save button in its bar already, is left alone. */
  const css = document.createElement("style");
  css.textContent = ".sheet-bar .topacts{display:flex;gap:8px;align-items:center;flex-shrink:0}.sheet-bar h2{min-width:0}" +
    ".sheet-bar .topsave{padding:8px 16px;font-size:.92rem;min-height:0}.sheet-bar .topsave[hidden]{display:none}";
  document.head.appendChild(css);
  const short = t => { t = (t || "").trim().split(/\s+[–—-]\s+/)[0]; return t.length <= 16 ? t : /save/i.test(t) ? "Save" : t.split(/\s+/)[0]; };
  function mainButton(sheet, bar) {
    return [...sheet.querySelectorAll("button.primary, button[type=submit]")]
      .find(b => !bar.contains(b) && !b.disabled && b.offsetParent !== null && b.textContent.trim());
  }
  function refresh(sheet) {
    const bar = sheet.querySelector(".sheet-bar"), top = bar && bar.querySelector("[data-topsave]");
    if (!top) return;
    const b = mainButton(sheet, bar), name = b ? short(b.textContent) : "";
    if (top.hidden !== !b) top.hidden = !b;                 // only real changes: the observer below sees our writes too
    if (b && top.textContent !== name) top.textContent = name;
  }
  function addTopSave() {
    document.querySelectorAll(".sheet").forEach(sheet => {
      const bar = sheet.querySelector(".sheet-bar");
      if (!bar || bar.querySelector("[data-topsave], button.primary, button[type=submit]")) return;
      const close = [...bar.children].find(e => e.tagName === "BUTTON");
      if (!close) return;
      const wrap = document.createElement("div"); wrap.className = "topacts";
      const top = document.createElement("button"); top.type = "button"; top.className = "primary topsave"; top.dataset.topsave = ""; top.hidden = true;
      top.addEventListener("click", () => { const b = mainButton(sheet, bar); if (b) b.click(); });
      close.replaceWith(wrap); wrap.append(top, close);
      // the main button can change while the sheet is open (another form shown, a button revealed)
      new MutationObserver(ms => { if (ms.some(m => m.target !== top)) refresh(sheet); }).observe(sheet, {attributes: true, subtree: true, attributeFilter: ["hidden", "disabled", "class"]});
      refresh(sheet);
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", addTopSave); else addTopSave();

  window.Nav = {
    FEATURES, DEFAULT,
    get: () => cur.slice(),
    /** Save a new choice of three; resolves to an error code or null. */
    async set(list) {
      if (!valid(list)) return "pick_three";
      let r;
      try {
        r = await fetch("api/nav", {method: "PUT", credentials: "same-origin",
          headers: {"Content-Type": "application/json", "X-Requested-With": "fetch"}, body: JSON.stringify({nav: list})});
      } catch (e) { return "offline"; }
      if (!r.ok) return "failed";
      cur = list.slice(); keep(cur); draw(cur); return null;
    },
  };
})();
