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
