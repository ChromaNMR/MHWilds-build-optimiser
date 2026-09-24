"""The optimiser.py command line, run as a real subprocess.

Only argument handling and the console header are tested here; the search
itself is covered in test_optimiser.py and costs the same either way.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support import REPO


def run_cli(*args: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPO / "optimiser.py"), *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, **env},
    )


class Arguments(unittest.TestCase):
    def test_out_of_range_values_are_rejected(self):
        for args, message in (
            (("--count", "0"), "--count must be at least 1"),
            (("--beam", "0"), "--beam must be at least 1"),
            (("--reserve", "-1"), "--reserve cannot be negative"),
        ):
            result = run_cli("--skills-db", "skills_weighted.yaml", *args)
            self.assertEqual(result.returncode, 2, args)
            self.assertIn(message, result.stderr)

    def test_bad_pin_is_rejected(self):
        result = run_cli("--skills-db", "skills_weighted.yaml", "--pin-head", "Nope")
        self.assertEqual(result.returncode, 2)
        self.assertIn("No armour piece is named", result.stderr)


class Header(unittest.TestCase):
    def test_strict_run_header_and_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "sets.yaml"
            result = run_cli(
                "--skills-db", "skills_weighted.yaml", "--count", "2",
                "--output", str(output),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("sets missing one are not shown", result.stdout)
            self.assertNotIn("Constraint tier", result.stdout)
            self.assertTrue(output.exists())

    def test_piped_output_survives_a_legacy_code_page(self):
        # Stands in for Windows, where piped stdout is cp1252 and armour
        # names are not encodable in it.
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli(
                "--skills-db", "skills_weighted.yaml", "--count", "1",
                "--output", str(Path(tmp) / "sets.yaml"),
                PYTHONIOENCODING="cp1252",
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Wrote 1 sets", result.stdout)
        self.assertTrue(any(letter in result.stdout for letter in "αβγ"))

    def test_usage_error_naming_a_piece_survives_a_legacy_code_page(self):
        result = run_cli(
            "--skills-db", "skills_weighted.yaml", "--pin-waist", "Lagiacrus Helm β",
            PYTHONIOENCODING="cp1252",
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("'Lagiacrus Helm β' is a head piece", result.stderr)

    def test_relax_names_the_tier(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli(
                "--skills-db", "skills_weighted.yaml", "--count", "1", "--relax",
                "--output", str(Path(tmp) / "sets.yaml"),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Constraint tier 0", result.stdout)


if __name__ == "__main__":
    unittest.main()
