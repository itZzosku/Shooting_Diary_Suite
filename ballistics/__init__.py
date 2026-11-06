"""Ballistic calculator for the Shooting Diary: profiles, holds and clicks for any range, range/dope/wind cards,
matches and stages, the reticle view and the shared reticle library.

Its own package next to diary/ and club/; everything it needs from the diary goes through bridge.py.
The trajectory is computed in the browser (static/solver.js) so it works offline at the range; the server keeps
the records and resolves a profile's links (gun, load, scope, scope zero, chrono velocities).

Addresses stay under the diary (same login, same installed app and offline cache):
  /diary/ballistics                  the page (pages/ballistics.html)
  /diary/api/ballistics/...          its API (api.py, reticles.py)
  /diary/static/ballistics/...       solver.js, drag.js, reticle.js (static/)

Data: per-account tables (bal_*) inside each account's diary database, so the diary's backups carry them;
the reticle library is one server-wide file, DIARY_DATA_DIR/library.db (python -m ballistics backup).
Design: "Ballistic Calculator — Design Document".
"""
import os

from flask import Blueprint

HERE = os.path.dirname(os.path.abspath(__file__))
PAGES = os.path.join(HERE, "pages")

bp = Blueprint("ballistics", __name__, static_folder="static", static_url_path="/static/ballistics")

from . import api, reticles  # noqa: E402  (routes register on bp)
from . import bridge  # noqa: E402


def init_app(app):
    """Register under /diary; make sure every account's database and the reticle library have their tables."""
    bridge.register("ballistics", api.init_ballistics, api.export_rows, api.import_rows)
    for path in bridge.account_dbs():
        conn = bridge.connect(path)
        try:
            api.init_ballistics(conn)
            conn.commit()
        finally:
            conn.close()
    reticles.init_library()
    app.register_blueprint(bp, url_prefix="/diary")


# Registered on import as well, so the diary's command line (backup / import) carries ballistics rows
# even without the web app.
bridge.register("ballistics", api.init_ballistics, api.export_rows, api.import_rows)
