"""Club ammo from the command line (run in the repo folder, with the venv's python):

  python -m club club-add "Club name" [--id SLUG] [--admin USERNAME]
                                         new club; the id in links is made from the name unless --id is given
  python -m club club-delete SLUG [--yes]  a club and everything in it (boxes, prices, the whole log)
  python -m club admin SLUG USERNAME [--remove]                  make a diary account a club admin
  python -m club list                                            clubs, boxes and stock
  python -m club backup DIR [--keep 12]                          clubs-YYYY-MM-DD.db into DIR; also forgets old names
"""
import argparse
import glob
import os
import sqlite3
import sys
import time

import club
from club import bridge

NAMES_YEARS = float(os.getenv("CLUB_NAMES_YEARS", "3"))   # shooters' names in the log are blanked after this


def forget_old_names(years=NAMES_YEARS):
    """The rounds and money stay for the statistics; who shot them is forgotten after `years`."""
    if years <= 0:
        return 0
    cutoff = time.strftime("%Y-%m-%d", time.gmtime(time.time() - years * 365.25 * 86400))
    with club.connect() as c:
        n = c.execute("UPDATE moves SET name='', uid=NULL, device=NULL WHERE kind='use' AND date<? AND name!=''",
                      (cutoff,)).rowcount
        n += c.execute("UPDATE sales SET name='', uid=NULL, device=NULL WHERE kind='sale' AND date<? AND name!=''",
                       (cutoff,)).rowcount
    return n


def backup(directory, keep):
    os.makedirs(directory, exist_ok=True)
    dst_path = os.path.join(directory, f"clubs-{time.strftime('%Y-%m-%d')}.db")
    src, dst = sqlite3.connect(club.db_path()), sqlite3.connect(dst_path)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    for p in sorted(glob.glob(os.path.join(directory, "clubs-[0-9]*.db")))[:-keep]:
        os.remove(p)
    return dst_path


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m club")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("club-add")
    a.add_argument("name")
    a.add_argument("--id", dest="slug", default="", help="id in links (default: from the name)")
    a.add_argument("--admin")
    d = sub.add_parser("club-delete")
    d.add_argument("slug")
    d.add_argument("--yes", action="store_true", help="also when it has boxes or entries")
    ad = sub.add_parser("admin")
    ad.add_argument("slug")
    ad.add_argument("username")
    ad.add_argument("--remove", action="store_true")
    sub.add_parser("list")
    b = sub.add_parser("backup")
    b.add_argument("directory")
    b.add_argument("--keep", type=int, default=12)
    sp = sub.add_parser("ssi", help="show what is read from an SSI event link (no changes)")
    sp.add_argument("url")
    a = p.parse_args(argv)
    if a.cmd == "ssi":
        from . import ssi
        try:
            r = ssi.fetch(a.url)
        except ssi.SsiError as e:
            sys.exit(str(e))
        print(f"Name:   {r['name']}\nDate:   {r['date'] or '?'}\nStages: {r['stages'] or '?'}\nRows:   {len(r['rows'])}")
        if r.get("warning"):
            print("Participants page:", r["warning"])
        for row in r["rows"][:4]:
            print("  | " + " | ".join(row))
        return
    club.init_db()

    if a.cmd in ("club-add", "admin"):
        name = a.admin if a.cmd == "club-add" else a.username
        u = bridge.user_by_name(name.lower()) if name else None
        if name and not u:
            sys.exit(f"No diary account called {name}")
        if a.cmd == "club-add":
            try:
                cid, slug = club.create_club(a.slug, a.name)
            except ValueError as e:
                sys.exit(str(e))
            print(f"Club {a.name}: /ammo/{slug}/admin")
        else:
            c = club.club_by_slug(a.slug, club.connect())
            if not c:
                sys.exit(f"No club {a.slug}")
            cid = c["id"]
        if u:
            club.set_role(cid, u["id"], None if getattr(a, "remove", False) else "admin")
            print(f"{u['username']}: {'removed' if getattr(a, 'remove', False) else 'admin'}")
    elif a.cmd == "club-delete":
        conn = club.connect()
        n = club.club_counts(a.slug, conn)
        if n is None:
            sys.exit(f"No club {a.slug}")
        if any(n.values()) and not a.yes:
            sys.exit(f"{a.slug} has {n['boxes']} boxes, {n['items']} ammo prices and {n['moves']} log rows – "
                     f"add --yes to delete it all (a backup first: python -m club backup DIR)")
        club.delete_club(a.slug, conn)
        conn.close()
        print(f"Deleted club {a.slug}")
    elif a.cmd == "list":
        conn = club.connect()
        for c in conn.execute("SELECT * FROM clubs ORDER BY slug"):
            print(f"{c['slug']}: {c['name']}  (MobilePay {c['mobilepay'] or '-'})")
            bs = club.box_stock(conn, c["id"])
            for bx in conn.execute("SELECT * FROM boxes WHERE club_id=? ORDER BY calibre, name", (c["id"],)):
                print(f"  {bx['name']:<14} {bx['calibre']:<14} {bs.get(bx['id'], 0):>6}  {bx['holder_name']}"
                      f"{'  (archived)' if bx['archived'] else ''}")
            for w in club.warnings(conn, c["id"]):
                print("  LOW:", w.get("name") or w.get("calibre"), w["n"], "<", w["low"])
        conn.close()
    elif a.cmd == "backup":
        n = forget_old_names()
        if n:
            print(f"Forgot the shooter's name on {n} entries older than {NAMES_YEARS:g} years")
        print("Backup clubs:", backup(a.directory, a.keep))


if __name__ == "__main__":
    main()
