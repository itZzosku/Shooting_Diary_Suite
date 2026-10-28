"""Runs the ballistic solver's JavaScript tests (tests/js) under Node, so pytest – on GitHub and in
scripts/update.sh – covers them too. Skipped when Node isn't installed (e.g. on the server)."""
import glob
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js not installed")
def test_solver_js():
    files = sorted(glob.glob(os.path.join(HERE, "js", "*.test.mjs")))
    assert files
    res = subprocess.run(["node", "--test", *files], capture_output=True, text=True, timeout=300)
    assert res.returncode == 0, res.stdout[-4000:] + res.stderr[-2000:]
