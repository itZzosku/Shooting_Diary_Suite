"""Runs the ballistic solver's JavaScript tests (tests/js) under Node, so pytest – on GitHub and in
scripts/update.sh – covers them too. Skipped when Node 18+ isn't installed (e.g. on the server)."""
import glob
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))


def _node_major():
    """Major version of the installed Node, or 0. `node --test` needs Node 18+; older distros ship older ones."""
    exe = shutil.which("node")
    if not exe:
        return 0
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
        return int(out.lstrip("v").split(".")[0])
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


@pytest.mark.skipif(_node_major() < 18, reason="Node.js 18+ not installed")
def test_solver_js():
    files = sorted(glob.glob(os.path.join(HERE, "js", "*.test.mjs")))
    assert files
    res = subprocess.run(["node", "--test", *files], capture_output=True, text=True, timeout=300)
    assert res.returncode == 0, res.stdout[-4000:] + res.stderr[-2000:]
