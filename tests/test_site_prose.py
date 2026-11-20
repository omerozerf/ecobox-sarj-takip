"""Runs the page's JavaScript unit tests (`tests/site/*.test.mjs`) with Node's own test runner,
so the Python suite covers them too. Skipped where Node is not installed."""

import shutil
import subprocess
import unittest
from pathlib import Path

SITE_TESTS = Path(__file__).resolve().parent / "site"


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class SiteJavaScriptTest(unittest.TestCase):
    def test_node_test_runner_passes(self):
        files = sorted(str(p) for p in SITE_TESTS.glob("*.test.mjs"))
        self.assertTrue(files)
        run = subprocess.run(["node", "--test", *files], capture_output=True, text=True,
                             timeout=60)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
