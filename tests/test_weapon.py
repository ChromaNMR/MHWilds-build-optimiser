"""Weapon slots and weapon-jewel placement."""

from __future__ import annotations

import itertools
import random
import unittest
from dataclasses import replace
from unittest import mock

from tests.support import Value, game, import_gui, weighted
from tests.test_cli import run_cli

import optimiser
from load_data import DecorationSlots, SkillLevel, Talisman
from optimiser import (
    Optimiser,
    Scoring,
    optimise,
    parse_weapon_slots,
    solve_weapon_slots,
)
from optimiser_report import render_set, render_set_inline

WEAPON_WEIGHTS = {"Attack Boost": (4, 3), "Critical Eye": (4, 3), "Artillery": (3, 2)}


def weapon_jewels():
    return [d for d in game().decorations if d.type == "weapon"]


def brute_force(sizes, scoring):
    """(tier, -score, gems) of the best layout, by trying every one."""
    useful = [
        d for d in weapon_jewels() if any(scoring.weight(s.name) > 0 for s in d.skills)
    ]
    order = sorted(sizes, reverse=True)
    best = None
    for combo in itertools.product(*[[None] + [d for d in useful if d.slot_level <= s] for s in order]):
        levels: dict[str, int] = {}
        for deco in filter(None, combo):
            for s in deco.skills:
                levels[s.name] = levels.get(s.name, 0) + s.level
        levels = {n: min(v, scoring.max_level(n)) for n, v in levels.items()}
        tier = 0
        for name in scoring.mandatory:
            if levels.get(name, 0) < 1:
                tier = 2
                break
            if name in scoring.mandatory_max and levels[name] < scoring.max_level(name):
                tier = 1
        value = sum(scoring.score(n, v) for n, v in levels.items())
        key = (tier, -round(value, 9), sum(1 for d in combo if d))
        best = key if best is None or key < best else best
    return best


class Parse(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(parse_weapon_slots("3,2,1"), (3, 2, 1))
        self.assertEqual(parse_weapon_slots(" 3 , 3 "), (3, 3))
        self.assertEqual(parse_weapon_slots(""), ())

    def test_invalid(self):
        for text in ("4", "0", "3,2,1,1", "a", "3;2"):
            with self.assertRaises(ValueError, msg=text):
                parse_weapon_slots(text)


class Solver(unittest.TestCase):
    def test_matches_brute_force(self):
        # Random weightings over real weapon jewels, including negative weights
        # and mandatory ones, against every possible layout.
        names = sorted({s.name for d in weapon_jewels() for s in d.skills})
        rng = random.Random(3)
        for _ in range(25):
            weights = {
                n: (rng.choice([5, 4, 3, 2, -1]), rng.choice([0, 2, 5]))
                for n in rng.sample(names, rng.choice([2, 3, 4]))
            }
            scoring = Scoring(weighted(weights))
            sizes = tuple(rng.choice([1, 2, 3]) for _ in range(rng.choice([1, 2, 3])))
            fill = solve_weapon_slots(sizes, game().decorations, scoring)
            mine = (fill.tier, -round(fill.score, 9), sum(1 for d in fill.decorations if d))
            self.assertEqual(mine, brute_force(sizes, scoring), (weights, sizes))
            self.assertTrue(fill.exact)

    def test_no_slots_is_empty(self):
        fill = solve_weapon_slots((), game().decorations, Scoring(weighted(WEAPON_WEIGHTS)))
        self.assertEqual((fill.decorations, fill.levels, fill.score), ((), {}, 0.0))

    def test_unwanted_skills_leave_slots_empty(self):
        fill = solve_weapon_slots((3, 2, 1), game().decorations, Scoring(weighted({})))
        self.assertEqual(fill.decorations, (None, None, None))

    def test_budget_marks_the_result_inexact(self):
        scoring = Scoring(weighted(WEAPON_WEIGHTS))
        with mock.patch.object(optimiser, "WEAPON_SEARCH_NODES", 3):
            fill = solve_weapon_slots((3, 3, 3), game().decorations, scoring)
        self.assertFalse(fill.exact)
        self.assertGreater(fill.score, 0)  # best-first, so still a real layout


class Search(unittest.TestCase):
    def test_weapon_skills_are_placed(self):
        sets, _, _ = optimise(
            game(), Scoring(weighted(WEAPON_WEIGHTS)), weapon_slots=(3, 2, 1)
        )
        self.assertTrue(sets)
        gear_set = sets[0]
        self.assertGreater(gear_set.skill_levels.get("Attack Boost", 0), 0)
        own = [p for p in gear_set.weapon_placements if p.source == "weapon"]
        self.assertEqual(sorted(p.size for p in own), [1, 2, 3])
        for placement in gear_set.weapon_placements:
            if placement.decoration is not None:
                self.assertEqual(placement.decoration.type, "weapon")
                self.assertLessEqual(placement.decoration.slot_level, placement.size)
        # Armour slots never receive a weapon jewel.
        for placement in gear_set.placements:
            if placement.decoration is not None:
                self.assertEqual(placement.decoration.type, "armor")

    def test_talisman_weapon_slots_are_filled(self):
        charm = Talisman(
            name="Test Weapon Charm",
            rarity=5,
            skills=[SkillLevel("Weakness Exploit", 3)],
            slots=[],
            decoration_slots=DecorationSlots(armour=[], weapon=[3]),
            source_url="custom",
        )
        data = replace(game(), talismans=list(game().talismans) + [charm])
        weights = dict(WEAPON_WEIGHTS, **{"Weakness Exploit": (4, 3)})
        sets, _, _ = optimise(data, Scoring(weighted(weights)), weapon_slots=(3,))
        chosen = [s for s in sets if s.talisman.name == charm.name]
        self.assertTrue(chosen, "a free extra size-3 weapon slot should win the charm slot")
        on_charm = [p for p in chosen[0].weapon_placements if p.source == charm.name]
        self.assertEqual(len(on_charm), 1)
        self.assertIsNotNone(on_charm[0].decoration)

    def test_bad_slots_raise(self):
        for slots in ((4,), (3, 2, 1, 1), (0,)):
            with self.assertRaises(ValueError, msg=slots):
                Optimiser(game(), Scoring(weighted({})), weapon_slots=slots)


class Proofs(unittest.TestCase):
    def reasons(self, weights, **kwargs):
        return Optimiser(game(), Scoring(weighted(weights)), **kwargs).impossible_requirements()

    def test_required_weapon_skill_without_slots(self):
        (reason,) = self.reasons({"Attack Boost": (5, 0)})
        self.assertIn("no weapon slots are set", reason)

    def test_required_weapon_skill_out_of_reach(self):
        # Attack Boost 5/5 cannot come from one size-1 slot.
        (reason,) = self.reasons({"Attack Boost": (5, 5)}, weapon_slots=(1,))
        self.assertIn("No layout of weapon decorations", reason)
        self.assertIn("Attack Boost 1/5", reason)

    def test_reachable_weapon_requirement_passes(self):
        self.assertEqual(self.reasons({"Attack Boost": (5, 0)}, weapon_slots=(1,)), [])

    def test_inexact_fill_proves_nothing(self):
        with mock.patch.object(optimiser, "WEAPON_SEARCH_NODES", 1):
            self.assertEqual(
                self.reasons({"Attack Boost": (5, 5)}, weapon_slots=(1,)), []
            )


class Report(unittest.TestCase):
    def test_weapon_line_and_markers(self):
        scoring = Scoring(weighted(WEAPON_WEIGHTS))
        sets, _, _ = optimise(game(), scoring, weapon_slots=(3, 2, 1))
        inline = render_set_inline(sets[0], 1, len(sets), scoring)
        self.assertIn("  weapon ", inline)
        self.assertIn("[W3: ", inline)
        console = render_set(sets[0], 1, scoring)
        self.assertIn("Free weapon slots", console)

    def test_no_weapon_line_without_weapon_slots(self):
        scoring = Scoring(weighted({"Weakness Exploit": (4, 3)}))
        sets, _, _ = optimise(game(), scoring)
        inline = render_set_inline(sets[0], 1, len(sets), scoring)
        self.assertFalse(any(line.startswith("  weapon ") for line in inline.splitlines()))
        self.assertNotIn("Free weapon slots", inline)


class Cli(unittest.TestCase):
    def test_bad_weapon_slots(self):
        result = run_cli("--skills-db", "skills_weighted.yaml", "--weapon-slots", "4")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--weapon-slots", result.stderr)


class Gui(unittest.TestCase):
    def test_zero_means_no_slot(self):
        G = import_gui()
        stub = type("Stub", (), {})()
        stub.weapon_slot_vars = [Value("3"), Value("0"), Value("1")]
        self.assertEqual(G.SkillsGui._weapon_slots(stub), (3, 1))


if __name__ == "__main__":
    unittest.main()
