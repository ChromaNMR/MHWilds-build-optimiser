"""GUI behaviour that does not need a window.

Each test calls a SkillsGui method with a stand-in for self carrying only the
attributes that method reads, and with messagebox patched so nothing pops up.
That covers the logic - what gets saved, refused or passed to the optimiser -
but not layout or widget wiring, which still needs a look by hand.
"""

from __future__ import annotations

import ast
import inspect
import queue
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from tests.support import Value, game, import_gui, weighted

import optimiser
from load_data import SKILLS_PATH, load_skills

G = import_gui()


def gui_stub(**attributes) -> types.SimpleNamespace:
    return types.SimpleNamespace(**attributes)


def last_message(results: queue.Queue):
    """The worker's final message, skipping the progress updates before it."""
    message = None
    while not results.empty():
        message = results.get_nowait()
    return message


class OptimiserCall(unittest.TestCase):
    def test_every_keyword_the_gui_passes_exists(self):
        # The bug this guards against: the GUI passed strict= to an optimise()
        # that had no such parameter, and Run failed on every click.
        tree = ast.parse(inspect.getsource(G.SkillsGui))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "optimise"
        ]
        self.assertTrue(calls, "the GUI no longer calls optimise()")
        accepted = set(inspect.signature(optimiser.optimise).parameters)
        for call in calls:
            passed = {k.arg for k in call.keywords}
            self.assertLessEqual(passed, accepted)
            self.assertLessEqual(len(call.args), 2)

    def test_worker_reports_sets_and_reasons(self):
        stub = gui_stub(game_data=game())
        results: queue.Queue = queue.Queue()
        easy = weighted({"Antivirus": (5, 0), "Weakness Exploit": (5, 0)})
        G.SkillsGui._optimiser_thread(stub, G.RunRequest(skills=easy), results)
        kind, result, _, _ = last_message(results)
        self.assertEqual((kind, len(result.sets), result.reasons), ("ok", 10, []))

        impossible = weighted({"Airborne": (5, 0)})
        G.SkillsGui._optimiser_thread(stub, G.RunRequest(skills=impossible), results)
        kind, result, _, _ = last_message(results)
        self.assertEqual((kind, result.sets), ("ok", []))
        self.assertIn("Airborne", result.reasons[0])


class GogmaOptions(unittest.TestCase):
    def test_only_bonuses_armour_carries_are_offered(self):
        stub = gui_stub(
            skills=game().skills,
            _armour_bonus_names={
                optimiser.bonus_base_name(b.name)
                for p in game().armor
                for b in p.set_bonuses
            },
            gogma_set_combo=mock.MagicMock(),
            gogma_group_combo=mock.MagicMock(),
            gogma_set_var=Value(G.NONE_OPTION),
            gogma_group_var=Value(G.NONE_OPTION),
        )
        G.SkillsGui._refresh_gogma_options(stub)
        offered = stub.gogma_set_combo.config.call_args.kwargs["values"]
        self.assertNotIn("Soul of the Dark Knight", offered)
        self.assertIn("Gore Magala's Tyranny", offered)


class SavingWeights(unittest.TestCase):
    def make_stub(self):
        skills = list(game().skills)
        stub = gui_stub(
            skills=skills,
            skills_by_name={s.name: s for s in skills},
            cache={
                s.name: {
                    "weight": G._weight_text(s.weight),
                    "level_weight": G._weight_text(s.level_weight),
                }
                for s in skills
            },
        )
        stub._effective_skills = lambda: G.SkillsGui._effective_skills(stub)
        return stub

    def test_unsaved_changes_tracked_through_a_save(self):
        stub = self.make_stub()
        self.assertFalse(G.SkillsGui._has_unsaved_changes(stub))
        stub.cache["Weakness Exploit"]["weight"] = "4"
        self.assertTrue(G.SkillsGui._has_unsaved_changes(stub))

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "weights.yaml"
            with mock.patch.object(G, "messagebox"):
                self.assertTrue(G.SkillsGui._write_skills(stub, target))
            saved = {s.name: s.weight for s in load_skills(target)}
        self.assertEqual(saved["Weakness Exploit"], 4.0)
        self.assertFalse(G.SkillsGui._has_unsaved_changes(stub))

    def test_default_skills_file_is_never_overwritten(self):
        stub = self.make_stub()
        before = Path(SKILLS_PATH).read_bytes()
        with mock.patch.object(G, "messagebox") as box:
            self.assertFalse(G.SkillsGui._write_skills(stub, Path(SKILLS_PATH)))
            box.showerror.assert_called_once()
        self.assertEqual(Path(SKILLS_PATH).read_bytes(), before)


class CustomTalismanValidation(unittest.TestCase):
    def save(self, rows):
        stub = gui_stub(
            custom_talisman_path=Path("unused.yaml"),
            ct_name_var=Value("Test"),
            ct_rarity_var=Value("5"),
            ct_skill_rows=[(Value(n), Value(l)) for n, l in rows],
            skills_by_name={s.name: s for s in game().skills},
            ct_armour_slot_vars=[Value("0")] * 3,
            ct_weapon_slot_vars=[Value("0")] * 3,
            custom_talismans=[],
            ct_selected_index=None,
            _write_custom_talismans=lambda _path: None,
            _refresh_custom_talisman_listbox=lambda: None,
            _clear_custom_talisman_form=lambda: None,
            ct_status_var=Value(),
        )
        with mock.patch.object(G, "messagebox") as box:
            G.SkillsGui._save_custom_talisman(stub)
        if box.showerror.called:
            return box.showerror.call_args.args[1]
        return stub.custom_talismans

    def test_duplicate_skill_refused(self):
        error = self.save([("Weakness Exploit", "2"), ("Weakness Exploit", "2")])
        self.assertIn("more than once", error)

    def test_level_below_one_refused(self):
        for level in ("0", "-2"):
            self.assertIn("at least 1", self.save([("Weakness Exploit", level)]))

    def test_level_above_max_refused(self):
        self.assertIn("only goes up to", self.save([("Weakness Exploit", "6")]))

    def test_valid_talisman_saved(self):
        (talisman,) = self.save([("Weakness Exploit", "2"), (G.NONE_OPTION, "1")])
        self.assertEqual([(s.name, s.level) for s in talisman.skills], [("Weakness Exploit", 2)])


if __name__ == "__main__":
    unittest.main()
