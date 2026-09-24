"""The skill list's filters, weight display and bulk clear."""

from __future__ import annotations

import types
import unittest
from unittest import mock

from tests.support import Value, game, import_gui

G = import_gui()


class FakeListbox:
    """The part of tk.Listbox the skill list uses, in memory."""

    def __init__(self):
        self.rows: list[str] = []
        self.selected: set[int] = set()

    def delete(self, first, last=None):
        if last is None:
            del self.rows[first]
        else:
            self.rows.clear()
        self.selected.clear()

    def insert(self, index, text):
        # tk.END is "end" with real Tk and a mock without it; either way,
        # anything that is not a row number means append.
        if isinstance(index, int):
            self.rows.insert(index, text)
        else:
            self.rows.append(text)

    def selection_set(self, index):
        self.selected.add(index)

    def see(self, _index):
        pass

    def curselection(self):
        return tuple(sorted(self.selected))


def make_stub(weights=None):
    skills = list(game().skills)
    stub = types.SimpleNamespace(
        skills=skills,
        cache={s.name: {"weight": "0", "level_weight": "0"} for s in skills},
        type_var=Value("All"),
        search_var=Value(""),
        weighted_only_var=Value(False),
        weighted_count_var=Value(""),
        listbox=FakeListbox(),
        selected_name=None,
        _listed_names=[],
        _show_details=mock.MagicMock(),
    )
    for name, (w, lw) in (weights or {}).items():
        stub.cache[name] = {"weight": w, "level_weight": lw}
    for method in (
        "_is_weighted",
        "_filtered_skills",
        "_row_text",
        "_refresh_listbox",
        "_update_weighted_count",
        "_refresh_selected_row",
    ):
        setattr(stub, method, getattr(G.SkillsGui, method).__get__(stub))
    stub._refresh_listbox()
    return stub


class Filters(unittest.TestCase):
    def test_search_ignores_case_and_matches_anywhere(self):
        stub = make_stub()
        stub.search_var.set("RESIST")
        stub._refresh_listbox()
        self.assertTrue(stub._listed_names)
        self.assertTrue(all("resist" in n.lower() for n in stub._listed_names))
        self.assertIn("Fire Resistance", stub._listed_names)

    def test_filters_combine(self):
        stub = make_stub({"Fire Resistance": ("2", "0"), "Weakness Exploit": ("5", "0")})
        stub.search_var.set("resist")
        stub.weighted_only_var.set(True)
        stub._refresh_listbox()
        self.assertEqual(stub._listed_names, ["Fire Resistance"])

    def test_type_filter_still_applies(self):
        stub = make_stub()
        stub.type_var.set("Food")
        stub._refresh_listbox()
        types_listed = {s.type for s in game().skills if s.name in stub._listed_names}
        self.assertEqual(types_listed, {"Food"})


class Rows(unittest.TestCase):
    def test_weighted_rows_show_their_weights(self):
        stub = make_stub({"Weakness Exploit": ("5", "0")})
        index = stub._listed_names.index("Weakness Exploit")
        self.assertEqual(stub.listbox.rows[index], "Weakness Exploit   5/0")
        other = stub._listed_names.index("Fire Resistance")
        self.assertEqual(stub.listbox.rows[other], "Fire Resistance")
        self.assertEqual(stub.weighted_count_var.get(), "1 weighted")

    def test_selection_survives_a_refresh(self):
        stub = make_stub()
        stub._show_details.reset_mock()  # the initial fill clears the panel
        stub.selected_name = "Fire Resistance"
        stub.search_var.set("fire")
        stub._refresh_listbox()
        index = stub._listed_names.index("Fire Resistance")
        self.assertEqual(stub.listbox.curselection(), (index,))
        stub._show_details.assert_not_called()

    def test_selection_dropped_when_filtered_out(self):
        stub = make_stub()
        stub.selected_name = "Fire Resistance"
        stub.search_var.set("weakness")
        stub._refresh_listbox()
        self.assertIsNone(stub.selected_name)
        stub._show_details.assert_called_with(None)

    def test_editing_a_weight_redraws_only_its_row(self):
        stub = make_stub()
        stub.selected_name = "Weakness Exploit"
        stub.cache["Weakness Exploit"]["weight"] = "4"
        stub._refresh_selected_row()
        index = stub._listed_names.index("Weakness Exploit")
        self.assertEqual(stub.listbox.rows[index], "Weakness Exploit   4/0")
        self.assertEqual(len(stub.listbox.rows), len(stub._listed_names))
        self.assertEqual(stub.listbox.curselection(), (index,))

    def test_select_maps_row_to_name(self):
        stub = make_stub({"Weakness Exploit": ("5", "0")})
        index = stub._listed_names.index("Weakness Exploit")
        stub.listbox.selection_set(index)
        G.SkillsGui._on_select(stub, None)
        self.assertEqual(stub.selected_name, "Weakness Exploit")


class ClearAll(unittest.TestCase):
    def test_clears_after_confirmation(self):
        stub = make_stub({"Weakness Exploit": ("5", "0"), "Attack Boost": ("3", "2")})
        with mock.patch.object(G, "messagebox") as box:
            box.askyesno.return_value = True
            G.SkillsGui._clear_all_weights(stub)
        self.assertFalse(any(stub._is_weighted(s.name) for s in stub.skills))
        self.assertEqual(stub.weighted_count_var.get(), "")

    def test_declining_keeps_weights(self):
        stub = make_stub({"Weakness Exploit": ("5", "0")})
        with mock.patch.object(G, "messagebox") as box:
            box.askyesno.return_value = False
            G.SkillsGui._clear_all_weights(stub)
        self.assertTrue(stub._is_weighted("Weakness Exploit"))

    def test_nothing_to_clear_asks_nothing(self):
        stub = make_stub()
        with mock.patch.object(G, "messagebox") as box:
            G.SkillsGui._clear_all_weights(stub)
            box.askyesno.assert_not_called()


if __name__ == "__main__":
    unittest.main()
