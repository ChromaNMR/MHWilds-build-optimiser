"""Saving and copying results from the GUI's results window."""

from __future__ import annotations

import queue
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import yaml

from tests.support import Value, game, import_gui, weighted

G = import_gui()

WEIGHTS = {"Antivirus": (5, 0), "Weakness Exploit": (5, 0), "Attack Boost": (4, 3)}


def finished_run():
    """A real RunResult, as the worker would post it."""
    stub = types.SimpleNamespace(game_data=game())
    results: queue.Queue = queue.Queue()
    request = G.RunRequest(
        skills=weighted(WEIGHTS),
        pinned_pieces={"waist": "Gore Coil α"},
        excluded_sets=["Lagiacrus β"],
        weapon_slots=(3, 2),
        source_label="skills_weighted.yaml (with unsaved edits)",
    )
    G.SkillsGui._optimiser_thread(stub, request, results)
    message = None
    while not results.empty():
        message = results.get_nowait()
    return message[1]


class Export(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = finished_run()

    def stub(self):
        stub = types.SimpleNamespace(
            game_data=game(),
            gear_result=self.result,
            gear_sets=self.result.sets,
            gear_scoring=self.result.scoring,
            gear_set_index=0,
            root=mock.MagicMock(),
            results_status_var=Value(),
            current_path=Path("skills_weighted.yaml"),
        )
        for name in ("_current_set_text", "_results_as_text", "_write_results"):
            setattr(stub, name, getattr(G.SkillsGui, name).__get__(stub))
        return stub

    def test_yaml_export_reads_back(self):
        stub = self.stub()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out" / "sets.yaml"
            stub._write_results(path)
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["sets"]), len(self.result.sets))
        self.assertIn("unsaved edits", payload["skills_db"])
        first = payload["sets"][0]
        self.assertTrue(any(d["weapon_slot"] for d in first["decorations"]))
        waist = next(p for p in first["pieces"] if p["piece_type"] == "waist")
        self.assertEqual((waist["name"], waist["pinned"]), ("Gore Coil α", True))

    def test_text_export_has_the_console_header(self):
        stub = self.stub()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sets.txt"
            stub._write_results(path)
            text = path.read_text(encoding="utf-8")
        self.assertIn("Pinned (*): waist Gore Coil α", text)
        self.assertIn("Excluded: Lagiacrus β", text)
        self.assertIn("sets missing one are not shown", text)
        self.assertEqual(text.count("\nSet "), len(self.result.sets))

    def test_copy_puts_the_shown_set_on_the_clipboard(self):
        stub = self.stub()
        stub.gear_set_index = 1
        G.SkillsGui._copy_current_set(stub)
        (copied,), _ = stub.root.clipboard_append.call_args
        self.assertTrue(copied.startswith(f"Set 2 of {len(self.result.sets)}"))
        self.assertEqual(stub.results_status_var.get(), "Set 2 copied.")


if __name__ == "__main__":
    unittest.main()
