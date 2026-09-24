"""Excluding armour sets and pieces from the search."""

from __future__ import annotations

import unittest

from tests.support import game, import_gui, weighted
from tests.test_cli import run_cli

from optimiser import Optimiser, Scoring, optimise, resolve_exclusions

EASY = {"Antivirus": (5, 0), "Weakness Exploit": (5, 0)}
GORE_SETS = ["Gore α", "Gore β", "Sororal α"]  # every carrier of the Tyranny bonus


class Resolve(unittest.TestCase):
    def test_sets_and_pieces_combine(self):
        excluded = resolve_exclusions(game(), ["Gore α"], ["Lagiacrus Helm β"])
        gore = {p.name for p in game().armor if p.set == "Gore α"}
        self.assertEqual(excluded, gore | {"Lagiacrus Helm β"})

    def test_unknown_names_raise(self):
        with self.assertRaisesRegex(ValueError, "No armour set"):
            resolve_exclusions(game(), ["Nope"], [])
        with self.assertRaisesRegex(ValueError, "No armour piece"):
            resolve_exclusions(game(), [], ["Nope"])

    def test_pinned_and_excluded_is_refused(self):
        with self.assertRaisesRegex(ValueError, "pinned .* but also excluded"):
            Optimiser(
                game(),
                Scoring(weighted({})),
                pinned_pieces={"waist": "Gore Coil α"},
                excluded_sets=["Gore α"],
            )


class Search(unittest.TestCase):
    def test_excluded_armour_never_appears(self):
        # Exclude whatever the unrestricted search likes best, so the
        # exclusion has to change the answer rather than pass trivially.
        first, _, _ = optimise(game(), Scoring(weighted(EASY)))
        favourite = first[0].pieces[0].set
        sets, _, _ = optimise(game(), Scoring(weighted(EASY)), excluded_sets=[favourite])
        self.assertTrue(sets)
        for gear_set in sets:
            self.assertNotIn(favourite, {p.set for p in gear_set.pieces})


class Proofs(unittest.TestCase):
    def reasons(self, weights, **kwargs):
        return Optimiser(game(), Scoring(weighted(weights)), **kwargs).impossible_requirements()

    def test_empty_slot_is_reported(self):
        heads = [p.name for p in game().armor if p.piece_type == "head"]
        self.assertIn(
            "Every head piece is excluded.", self.reasons({}, excluded_pieces=heads)
        )

    def test_skill_only_on_excluded_armour(self):
        # Palico Rally comes from no talisman or jewel, only these four sets.
        (reason,) = self.reasons(
            {"Palico Rally": (5, 0)},
            excluded_sets=["Blango α", "Clerk α", "Faux Felyne α", "Kunafa α"],
        )
        self.assertIn("only excluded armour provides it", reason)

    def test_bonus_carriers_respect_exclusions(self):
        (reason,) = self.reasons(
            {"Gore Magala's Tyranny": (5, 0)}, excluded_sets=GORE_SETS
        )
        self.assertIn("only 0 free slot(s)", reason)


class GuiStub(unittest.TestCase):
    """Stand-in for SkillsGui carrying only the exclusion state."""

    def setUp(self):
        G = import_gui()
        self.G = G
        self.stub = type("Stub", (), {})()
        self.stub.excluded_sets = set()
        self.stub.excluded_pieces = set()
        self.stub.set_of_piece = {p.name: p.set for p in game().armor}
        self.stub.exclusion_summary_var = type("V", (), {"set": lambda s, v: None})()
        self.stub._exclusion_summary = lambda: G.SkillsGui._exclusion_summary(self.stub)

    def toggle(self, *iids):
        self.G.SkillsGui._toggle_exclusions(self.stub, iids)

    def state(self, iid):
        return self.G.SkillsGui._exclusion_state(self.stub, iid)


class GuiToggle(GuiStub):
    def test_toggle_round_trip(self):
        self.toggle("set:Gore α", "piece:Lagiacrus Helm β")
        self.assertEqual(self.stub.excluded_sets, {"Gore α"})
        self.assertEqual(self.stub.excluded_pieces, {"Lagiacrus Helm β"})
        self.assertEqual(self.state("piece:Gore Coil α"), "excluded (set)")
        self.toggle("set:Gore α", "piece:Lagiacrus Helm β")
        self.assertEqual((self.stub.excluded_sets, self.stub.excluded_pieces), (set(), set()))

    def test_piece_in_excluded_set_is_left_alone(self):
        self.toggle("set:Gore α")
        self.toggle("piece:Gore Coil α")
        self.assertEqual(self.stub.excluded_pieces, set())

    def test_summary(self):
        G = self.G
        self.assertEqual(G.SkillsGui._exclusion_summary(self.stub), "Nothing excluded")
        self.toggle("set:Gore α", "piece:Lagiacrus Helm β")
        self.assertEqual(
            G.SkillsGui._exclusion_summary(self.stub), "Excluded: 1 set(s), 1 piece(s)"
        )


class FakeTree:
    """The part of ttk.Treeview the exclusion dialog uses, in memory."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.children: dict[str, list[str]] = {"": []}

    def insert(self, parent, _index, iid, text, values, open=False):
        self.rows[iid] = {"text": text, "values": values, "open": open, "tags": ()}
        self.children.setdefault(parent, []).append(iid)
        self.children.setdefault(iid, [])

    def get_children(self, iid=""):
        return tuple(self.children.get(iid, []))

    def delete(self, *iids):
        for iid in iids:
            for child in self.children.pop(iid, []):
                self.rows.pop(child, None)
            self.rows.pop(iid, None)
        self.children[""] = [i for i in self.children[""] if i not in iids]

    def item(self, iid, values, tags):
        self.rows[iid].update(values=values, tags=tags)

    def tag_configure(self, *_args, **_kwargs):
        pass


class GuiTree(GuiStub):
    def fill(self, text):
        stub = self.stub
        stub.exclusion_tree = FakeTree()
        stub.exclusion_filter_var = type("V", (), {"get": lambda s: text})()
        stub.game_data = game()
        stub.dark_var = type("V", (), {"get": lambda s: False})()
        stub._exclusion_state = lambda iid: self.G.SkillsGui._exclusion_state(stub, iid)
        stub._refresh_exclusion_states = lambda: self.G.SkillsGui._refresh_exclusion_states(stub)
        self.G.SkillsGui._fill_exclusion_tree(stub)
        return stub.exclusion_tree

    def test_unfiltered_tree_holds_every_piece_once(self):
        tree = self.fill("")
        pieces = [iid for iid in tree.rows if iid.startswith("piece:")]
        self.assertEqual(len(pieces), len(game().armor))
        self.assertFalse(any(tree.rows[s]["open"] for s in tree.get_children()))

    def test_filter_on_piece_name_opens_only_matching_sets(self):
        tree = self.fill("gore coil")
        self.assertEqual(tree.get_children(), ("set:Gore α", "set:Gore β"))
        self.assertTrue(tree.rows["set:Gore α"]["open"])
        self.assertEqual(tree.get_children("set:Gore α"), ("piece:Gore Coil α",))

    def test_filter_on_set_name_keeps_all_its_pieces(self):
        tree = self.fill("gore α")
        self.assertEqual(len(tree.get_children("set:Gore α")), 5)

    def test_status_column_follows_toggles(self):
        tree = self.fill("gore")
        self.toggle("set:Gore α")
        self.G.SkillsGui._refresh_exclusion_states(self.stub)
        self.assertEqual(tree.rows["set:Gore α"]["values"], ("excluded",))
        self.assertEqual(tree.rows["piece:Gore Coil α"]["values"], ("excluded (set)",))
        self.assertEqual(tree.rows["piece:Gore Coil α"]["tags"], ("excluded",))


class Cli(unittest.TestCase):
    def test_unknown_set_is_a_usage_error(self):
        result = run_cli("--skills-db", "skills_weighted.yaml", "--exclude-set", "Nope")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Excluded set 'Nope' does not exist", result.stderr)

    def test_pinned_and_excluded_is_a_usage_error(self):
        result = run_cli(
            "--skills-db", "skills_weighted.yaml",
            "--pin-waist", "Gore Coil α", "--exclude-set", "Gore α",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("also excluded", result.stderr)


if __name__ == "__main__":
    unittest.main()
