"""Ballistics from the command line (run in the repo folder, with the venv's python):

  python -m ballistics backup DIR [--keep 12]   library-YYYY-MM-DD.db (the shared reticle library) into DIR

Per-account ballistics data (profiles, bullets, matches, stages) is in each account's diary database and in the
diary's own backups (python -m diary backup).
"""
import argparse

from . import reticles


def main():
    p = argparse.ArgumentParser(prog="python -m ballistics")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup", help="copy the reticle library into DIR")
    b.add_argument("directory")
    b.add_argument("--keep", type=int, default=12)
    a = p.parse_args()
    if a.cmd == "backup":
        import os
        os.makedirs(a.directory, exist_ok=True)
        path = reticles.backup(a.directory, a.keep)
        print(f"Backup reticle library: {path} (keeping the newest {a.keep})" if path else "No reticle library yet.")


if __name__ == "__main__":
    main()
