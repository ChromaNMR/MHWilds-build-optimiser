"""GUI for assigning weight / level_weight to skills, building custom
talismans, and running the gear set optimiser.

Filter skills by type, edit their weight and level_weight, then save a
copy of skills.yaml (with the edited values). A second tab lets the user
build custom talismans (not present in craftable_talismans.yaml) and save
them to their own file; the main tab can load such a file and fold those
talismans into the optimiser's talisman pool without touching the yaml.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from dataclasses import asdict, replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import yaml

from load_data import (
    DATA_DIR,
    SKILLS_PATH,
    DecorationSlots,
    GameData,
    Skill,
    SkillLevel,
    Talisman,
    load_game_data,
    load_skills,
    load_talismans,
)
from optimiser import RESERVED_SLOTS, GearSet, Scoring, optimise
from optimiser_report import render_set_inline

NONE_OPTION = "(None)"
CUSTOM_TALISMANS_DIR = DATA_DIR / "custom_talismans_outputs"
MAX_TALISMAN_SKILLS = 3
MAX_TALISMAN_SLOTS = 3


def _slot_sizes(value, default_size: int = 1) -> list[int]:
    """Normalise a decoration_slots.armour/weapon value to a list of sizes."""
    if isinstance(value, (list, tuple)):
        return [int(s) for s in value if s]
    return [default_size] * int(value or 0)


class SkillsGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("MHWilds Skill Weights")
        self.root.geometry("760x680")

        self.current_path: Path = Path(SKILLS_PATH)
        self.skills: list[Skill] = []
        self.skills_by_name: dict[str, Skill] = {}
        self.cache: dict[str, dict[str, str]] = {}
        self.output_dir: Path = self.current_path.parent

        self.selected_name: str | None = None
        self._suppress_trace = False

        # Optimiser integration state.
        self.game_data: GameData | None = None
        self._optimiser_running = False
        self.gear_sets: list[GearSet] = []
        self.gear_scoring: Scoring | None = None
        self.gear_set_index = 0
        self.results_window: tk.Toplevel | None = None

        # Custom talismans loaded (via the main tab) for the optimiser to use,
        # kept separate from craftable_talismans.yaml entirely.
        self.custom_talismans_loaded: list[Talisman] = []
        self.custom_talismans_source: Path | None = None

        # Custom Talismans tab editing state.
        self.custom_talisman_path: Path | None = None
        self.custom_talismans: list[Talisman] = []
        self.ct_selected_index: int | None = None

        self._build_widgets()
        self._load_file(self.current_path)

    # --- layout ------------------------------------------------------------

    def _build_widgets(self) -> None:
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        skills_tab = ttk.Frame(self.notebook)
        talismans_tab = ttk.Frame(self.notebook)
        self.notebook.add(skills_tab, text="Skill Weights")
        self.notebook.add(talismans_tab, text="Custom Talismans")

        self._build_skills_tab(skills_tab)
        self._build_custom_talismans_tab(talismans_tab)

    def _build_skills_tab(self, parent: ttk.Frame) -> None:
        file_row = ttk.Frame(parent, padding=(8, 8, 8, 0))
        file_row.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(file_row, text="Open...", command=self._open_file).pack(side=tk.LEFT)
        self.file_label_var = tk.StringVar(value="")
        ttk.Label(file_row, textvariable=self.file_label_var, foreground="#555555").pack(
            side=tk.LEFT, padx=(8, 0)
        )

        custom_row = ttk.Frame(parent, padding=(8, 4, 8, 0))
        custom_row.pack(side=tk.TOP, fill=tk.X)
        ttk.Button(
            custom_row,
            text="Load Custom Talismans...",
            command=self._load_custom_talismans_for_optimiser,
        ).pack(side=tk.LEFT)
        ttk.Button(
            custom_row, text="Clear", command=self._clear_loaded_custom_talismans
        ).pack(side=tk.LEFT, padx=(4, 0))
        self.custom_talismans_status_var = tk.StringVar(value="Custom talismans: none loaded")
        ttk.Label(
            custom_row, textvariable=self.custom_talismans_status_var, foreground="#555555"
        ).pack(side=tk.LEFT, padx=(8, 0))

        top = ttk.Frame(parent, padding=8)
        top.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(top, text="Type:").pack(side=tk.LEFT)
        self.type_var = tk.StringVar(value="All")
        self.type_combo = ttk.Combobox(
            top, textvariable=self.type_var, values=["All"], state="readonly", width=15
        )
        self.type_combo.pack(side=tk.LEFT, padx=(4, 0))
        self.type_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_listbox())

        gogma = ttk.LabelFrame(parent, text="Gogma weapon skills", padding=8)
        gogma.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(0, 8))
        ttk.Label(gogma, text="Set Bonus:").pack(side=tk.LEFT)
        self.gogma_set_var = tk.StringVar(value=NONE_OPTION)
        self.gogma_set_combo = ttk.Combobox(
            gogma,
            textvariable=self.gogma_set_var,
            values=[NONE_OPTION],
            state="readonly",
            width=26,
        )
        self.gogma_set_combo.pack(side=tk.LEFT, padx=(4, 16))

        ttk.Label(gogma, text="Group Skill:").pack(side=tk.LEFT)
        self.gogma_group_var = tk.StringVar(value=NONE_OPTION)
        self.gogma_group_combo = ttk.Combobox(
            gogma,
            textvariable=self.gogma_group_var,
            values=[NONE_OPTION],
            state="readonly",
            width=26,
        )
        self.gogma_group_combo.pack(side=tk.LEFT, padx=(4, 0))

        body = ttk.Frame(parent, padding=8)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        list_frame = ttk.Frame(body)
        list_frame.pack(side=tk.LEFT, fill=tk.Y)

        self.listbox = tk.Listbox(list_frame, width=35, exportselection=False)
        self.listbox.pack(side=tk.LEFT, fill=tk.Y)
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.listbox.yview)
        scrollbar.pack(side=tk.LEFT, fill=tk.Y)
        self.listbox.config(yscrollcommand=scrollbar.set)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

        detail = ttk.Frame(body, padding=(16, 0))
        detail.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.name_label = ttk.Label(detail, text="", font=("Segoe UI", 12, "bold"))
        self.name_label.pack(anchor=tk.W)

        self.info_label = ttk.Label(detail, text="", foreground="#555555")
        self.info_label.pack(anchor=tk.W, pady=(0, 4))

        self.desc_label = ttk.Label(detail, text="", wraplength=380, justify=tk.LEFT)
        self.desc_label.pack(anchor=tk.W, pady=(0, 16))

        form = ttk.Frame(detail)
        form.pack(anchor=tk.W)

        ttk.Label(form, text="Weight:").grid(row=0, column=0, sticky=tk.W, pady=4)
        self.weight_var = tk.StringVar()
        self.weight_entry = ttk.Entry(form, textvariable=self.weight_var, width=12)
        self.weight_entry.grid(row=0, column=1, padx=(8, 0))
        self.weight_var.trace_add("write", self._on_weight_change)

        ttk.Label(form, text="Level Weight:").grid(row=1, column=0, sticky=tk.W, pady=4)
        self.level_weight_var = tk.StringVar()
        self.level_weight_entry = ttk.Entry(form, textvariable=self.level_weight_var, width=12)
        self.level_weight_entry.grid(row=1, column=1, padx=(8, 0))
        self.level_weight_var.trace_add("write", self._on_level_weight_change)

        self.weight_entry.config(state=tk.DISABLED)
        self.level_weight_entry.config(state=tk.DISABLED)

        # Packed side=BOTTOM in this order so 'bottom' lands at the very
        # bottom edge and 'output_row' stacks just above it.
        bottom = ttk.Frame(parent, padding=8)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_var = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.status_var, foreground="#2a7f2a").pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )
        self.run_button = ttk.Button(
            bottom, text="Run Optimiser", command=self._run_optimiser
        )
        self.run_button.pack(side=tk.RIGHT)

        self.reserved_slots_var = tk.StringVar(value=str(RESERVED_SLOTS))
        ttk.Spinbox(
            bottom,
            from_=0,
            to=10,
            textvariable=self.reserved_slots_var,
            width=3,
            justify=tk.CENTER,
        ).pack(side=tk.RIGHT, padx=(0, 8))
        ttk.Label(bottom, text="Reserved level-1 slots:").pack(side=tk.RIGHT, padx=(8, 0))

        output_row = ttk.Frame(parent, padding=(8, 0, 8, 4))
        output_row.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(output_row, text="Output file:").pack(side=tk.LEFT)
        self.output_name_var = tk.StringVar(value="skills_weighted.yaml")
        ttk.Entry(output_row, textvariable=self.output_name_var, width=28).pack(
            side=tk.LEFT, padx=(4, 4)
        )
        ttk.Button(output_row, text="Browse...", command=self._browse_save).pack(side=tk.LEFT)
        ttk.Button(output_row, text="Save", command=self._save).pack(side=tk.LEFT, padx=(4, 0))

    def _build_custom_talismans_tab(self, parent: ttk.Frame) -> None:
        file_row = ttk.Frame(parent, padding=8)
        file_row.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(file_row, text="File:").pack(side=tk.LEFT)
        self.ct_file_var = tk.StringVar(value="(none selected)")
        ttk.Label(file_row, textvariable=self.ct_file_var, foreground="#555555").pack(
            side=tk.LEFT, padx=(4, 8)
        )
        ttk.Button(
            file_row, text="Select/Create File...", command=self._select_custom_talisman_file
        ).pack(side=tk.LEFT)

        body = ttk.Frame(parent, padding=8)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        list_frame = ttk.Frame(body)
        list_frame.pack(side=tk.LEFT, fill=tk.Y)
        self.ct_listbox = tk.Listbox(list_frame, width=30, exportselection=False)
        self.ct_listbox.pack(side=tk.TOP, fill=tk.Y, expand=True)
        ct_scroll = ttk.Scrollbar(
            list_frame, orient=tk.VERTICAL, command=self.ct_listbox.yview
        )
        self.ct_listbox.config(yscrollcommand=ct_scroll.set)
        self.ct_listbox.bind("<<ListboxSelect>>", self._on_custom_talisman_select)
        self.ct_delete_button = ttk.Button(
            list_frame, text="Delete Selected", command=self._delete_custom_talisman
        )
        self.ct_delete_button.pack(side=tk.TOP, fill=tk.X, pady=(8, 0))

        form = ttk.Frame(body, padding=(16, 0))
        form.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        ttk.Label(form, text="Name:").grid(row=0, column=0, sticky=tk.W, pady=4)
        self.ct_name_var = tk.StringVar()
        self.ct_name_entry = ttk.Entry(form, textvariable=self.ct_name_var, width=30)
        self.ct_name_entry.grid(row=0, column=1, columnspan=3, sticky=tk.W, padx=(8, 0))

        ttk.Label(form, text="Rarity:").grid(row=1, column=0, sticky=tk.W, pady=4)
        self.ct_rarity_var = tk.StringVar(value="5")
        self.ct_rarity_spin = ttk.Spinbox(
            form, from_=1, to=8, textvariable=self.ct_rarity_var, width=5, justify=tk.CENTER
        )
        self.ct_rarity_spin.grid(row=1, column=1, sticky=tk.W, padx=(8, 0))

        ttk.Label(form, text="Skills:").grid(row=2, column=0, sticky=tk.NW, pady=(8, 4))
        self.ct_skill_widgets: list[tuple[ttk.Combobox, ttk.Spinbox]] = []
        skill_row_vars: list[tuple[tk.StringVar, tk.StringVar]] = []
        for i in range(MAX_TALISMAN_SKILLS):
            name_var = tk.StringVar(value=NONE_OPTION)
            level_var = tk.StringVar(value="1")
            name_combo = ttk.Combobox(
                form, textvariable=name_var, values=[NONE_OPTION], state="readonly", width=26
            )
            name_combo.grid(row=2 + i, column=1, sticky=tk.W, padx=(8, 4), pady=2)
            level_spin = ttk.Spinbox(
                form, from_=1, to=5, textvariable=level_var, width=4, justify=tk.CENTER
            )
            level_spin.grid(row=2 + i, column=2, sticky=tk.W, pady=2)
            self.ct_skill_widgets.append((name_combo, level_spin))
            skill_row_vars.append((name_var, level_var))
        self.ct_skill_rows = skill_row_vars

        slot_row = 2 + MAX_TALISMAN_SKILLS
        ttk.Label(form, text="Armour Slots:").grid(
            row=slot_row, column=0, sticky=tk.W, pady=(12, 4)
        )
        self.ct_armour_slot_widgets: list[ttk.Combobox] = []
        self.ct_armour_slot_vars: list[tk.StringVar] = []
        for i in range(MAX_TALISMAN_SLOTS):
            var = tk.StringVar(value="0")
            combo = ttk.Combobox(
                form, textvariable=var, values=["0", "1", "2", "3"], state="readonly", width=4
            )
            combo.grid(row=slot_row, column=1 + i, sticky=tk.W, padx=(8 if i == 0 else 4, 0))
            self.ct_armour_slot_widgets.append(combo)
            self.ct_armour_slot_vars.append(var)

        ttk.Label(form, text="Weapon Slots:").grid(
            row=slot_row + 1, column=0, sticky=tk.W, pady=(4, 4)
        )
        self.ct_weapon_slot_widgets: list[ttk.Combobox] = []
        self.ct_weapon_slot_vars: list[tk.StringVar] = []
        for i in range(MAX_TALISMAN_SLOTS):
            var = tk.StringVar(value="0")
            combo = ttk.Combobox(
                form, textvariable=var, values=["0", "1", "2", "3"], state="readonly", width=4
            )
            combo.grid(row=slot_row + 1, column=1 + i, sticky=tk.W, padx=(8 if i == 0 else 4, 0))
            self.ct_weapon_slot_widgets.append(combo)
            self.ct_weapon_slot_vars.append(var)

        button_row = slot_row + 2
        buttons = ttk.Frame(form)
        buttons.grid(row=button_row, column=0, columnspan=4, sticky=tk.W, pady=(16, 0))
        self.ct_new_button = ttk.Button(
            buttons, text="New", command=self._clear_custom_talisman_form
        )
        self.ct_new_button.pack(side=tk.LEFT)
        self.ct_save_button = ttk.Button(
            buttons, text="Save Talisman", command=self._save_custom_talisman
        )
        self.ct_save_button.pack(side=tk.LEFT, padx=(8, 0))

        status_row = ttk.Frame(parent, padding=8)
        status_row.pack(side=tk.BOTTOM, fill=tk.X)
        self.ct_status_var = tk.StringVar(value="")
        ttk.Label(status_row, textvariable=self.ct_status_var, foreground="#2a7f2a").pack(
            side=tk.LEFT
        )

        self._set_custom_talisman_controls_enabled(False)

    def _on_tab_changed(self, _event: object) -> None:
        if (
            self.notebook.index(self.notebook.select()) == 1
            and self.custom_talisman_path is None
        ):
            self._select_custom_talisman_file()

    # --- skills tab: file handling ------------------------------------------

    def _open_file(self) -> None:
        path_str = filedialog.askopenfilename(
            title="Open skills file",
            initialdir=str(self.current_path.parent),
            initialfile=self.current_path.name,
            filetypes=[("YAML files", "*.yaml *.yml"), ("All files", "*.*")],
        )
        if not path_str:
            return
        self._load_file(Path(path_str))

    def _load_file(self, path: Path) -> None:
        try:
            skills = load_skills(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Failed to load file", f"Could not load {path}:\n{exc}")
            return

        self.current_path = path
        self.output_dir = path.parent
        self.skills = skills
        self.skills_by_name = {s.name: s for s in self.skills}
        self.cache = {
            s.name: {"weight": str(s.weight), "level_weight": str(s.level_weight)}
            for s in self.skills
        }
        self.selected_name = None
        self.status_var.set("")

        self.file_label_var.set(str(self.current_path))
        types = ["All"] + sorted({s.type for s in self.skills})
        self.type_combo.config(values=types)
        self.type_var.set("All")

        self._refresh_gogma_options()
        self._refresh_listbox()
        self._refresh_custom_talisman_skill_options()

    def _refresh_gogma_options(self) -> None:
        set_names = [NONE_OPTION] + sorted(
            s.name for s in self.skills if s.type == "Set Bonus"
        )
        group_names = [NONE_OPTION] + sorted(
            s.name for s in self.skills if s.type == "Group"
        )
        self.gogma_set_combo.config(values=set_names)
        self.gogma_group_combo.config(values=group_names)
        if self.gogma_set_var.get() not in set_names:
            self.gogma_set_var.set(NONE_OPTION)
        if self.gogma_group_var.get() not in group_names:
            self.gogma_group_var.set(NONE_OPTION)

    def _filtered_skills(self) -> list[Skill]:
        selected_type = self.type_var.get()
        if selected_type == "All":
            return self.skills
        return [s for s in self.skills if s.type == selected_type]

    def _refresh_listbox(self) -> None:
        self.listbox.delete(0, tk.END)
        for skill in self._filtered_skills():
            self.listbox.insert(tk.END, skill.name)
        self.selected_name = None
        self._show_details(None)

    def _on_select(self, _event: object) -> None:
        selection = self.listbox.curselection()
        if not selection:
            return
        name = self.listbox.get(selection[0])
        self.selected_name = name
        self._show_details(name)

    def _show_details(self, name: str | None) -> None:
        self._suppress_trace = True
        try:
            if name is None:
                self.name_label.config(text="")
                self.info_label.config(text="")
                self.desc_label.config(text="")
                self.weight_var.set("")
                self.level_weight_var.set("")
                self.weight_entry.config(state=tk.DISABLED)
                self.level_weight_entry.config(state=tk.DISABLED)
                return

            skill = self.skills_by_name[name]
            self.name_label.config(text=skill.name)
            self.info_label.config(
                text=f"Type: {skill.type}   Max Level: {skill.max_level}   Scaling: {skill.scaling}"
            )
            self.desc_label.config(text=skill.description)

            cached = self.cache[name]
            self.weight_var.set(cached["weight"])
            self.level_weight_var.set(cached["level_weight"])
            self.weight_entry.config(state=tk.NORMAL)
            self.level_weight_entry.config(state=tk.NORMAL)
        finally:
            self._suppress_trace = False

    def _on_weight_change(self, *_args: object) -> None:
        if self._suppress_trace or self.selected_name is None:
            return
        self.cache[self.selected_name]["weight"] = self.weight_var.get()

    def _on_level_weight_change(self, *_args: object) -> None:
        if self._suppress_trace or self.selected_name is None:
            return
        self.cache[self.selected_name]["level_weight"] = self.level_weight_var.get()

    def _effective_skills(self) -> list[Skill]:
        """The skills list with every cached (possibly unsaved) edit applied."""
        result = []
        for skill in self.skills:
            cached = self.cache[skill.name]
            try:
                weight = float(cached["weight"])
            except ValueError:
                weight = skill.weight
            try:
                level_weight = float(cached["level_weight"])
            except ValueError:
                level_weight = skill.level_weight
            result.append(replace(skill, weight=weight, level_weight=level_weight))
        return result

    def _write_skills(self, output_path: Path) -> None:
        output = [asdict(skill) for skill in self._effective_skills()]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as f:
            yaml.dump(output, f, sort_keys=False, allow_unicode=True, default_flow_style=False)

    def _save(self) -> None:
        filename = self.output_name_var.get().strip()
        if not filename:
            messagebox.showerror("Save", "Enter an output file name.")
            return
        if not filename.lower().endswith((".yaml", ".yml")):
            filename += ".yaml"
            self.output_name_var.set(filename)

        output_path = self.output_dir / filename
        self._write_skills(output_path)
        self.status_var.set(f"Saved to {output_path.name}")
        messagebox.showinfo("Saved", f"Saved weights to {output_path}")

    def _browse_save(self) -> None:
        path_str = filedialog.asksaveasfilename(
            title="Save weighted skills as",
            initialdir=str(self.output_dir),
            initialfile=self.output_name_var.get() or "skills_weighted.yaml",
            defaultextension=".yaml",
            filetypes=[("YAML files", "*.yaml *.yml"), ("All files", "*.*")],
        )
        if not path_str:
            return
        output_path = Path(path_str)
        self.output_dir = output_path.parent
        self.output_name_var.set(output_path.name)

        self._write_skills(output_path)
        self.status_var.set(f"Saved to {output_path.name}")
        messagebox.showinfo("Saved", f"Saved weights to {output_path}")

    # --- custom talismans: loading a file for the optimiser to use ---------

    def _load_custom_talismans_for_optimiser(self) -> None:
        initial_dir = (
            self.custom_talisman_path.parent
            if self.custom_talisman_path is not None
            else CUSTOM_TALISMANS_DIR
        )
        CUSTOM_TALISMANS_DIR.mkdir(parents=True, exist_ok=True)
        path_str = filedialog.askopenfilename(
            title="Load custom talismans for the optimiser",
            initialdir=str(initial_dir),
            filetypes=[("YAML files", "*.yaml *.yml"), ("All files", "*.*")],
        )
        if not path_str:
            return
        path = Path(path_str)
        try:
            talismans = load_talismans(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Failed to load file", f"Could not load {path}:\n{exc}")
            return

        self.custom_talismans_loaded = talismans
        self.custom_talismans_source = path
        self.custom_talismans_status_var.set(
            f"Custom talismans: {len(talismans)} loaded from {path.name}"
        )

    def _clear_loaded_custom_talismans(self) -> None:
        self.custom_talismans_loaded = []
        self.custom_talismans_source = None
        self.custom_talismans_status_var.set("Custom talismans: none loaded")

    # --- custom talismans tab: building/editing -----------------------------

    def _refresh_custom_talisman_skill_options(self) -> None:
        names = [NONE_OPTION] + sorted(
            s.name for s in self.skills if s.type == "Armor"
        )
        for name_combo, _level_spin in self.ct_skill_widgets:
            name_combo.config(values=names)
        for name_var, _level_var in self.ct_skill_rows:
            if name_var.get() not in names:
                name_var.set(NONE_OPTION)

    def _set_custom_talisman_controls_enabled(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        combo_state = "readonly" if enabled else tk.DISABLED
        self.ct_name_entry.config(state=state)
        self.ct_rarity_spin.config(state=state)
        for name_combo, level_spin in self.ct_skill_widgets:
            name_combo.config(state=combo_state)
            level_spin.config(state=state)
        for combo in self.ct_armour_slot_widgets + self.ct_weapon_slot_widgets:
            combo.config(state=combo_state)
        self.ct_save_button.config(state=state)
        self.ct_delete_button.config(state=state)
        self.ct_new_button.config(state=state)

    def _select_custom_talisman_file(self) -> None:
        CUSTOM_TALISMANS_DIR.mkdir(parents=True, exist_ok=True)
        path_str = filedialog.asksaveasfilename(
            title="Select or create a custom talismans file",
            initialdir=str(CUSTOM_TALISMANS_DIR),
            initialfile="my_talismans.yaml",
            defaultextension=".yaml",
            filetypes=[("YAML files", "*.yaml *.yml"), ("All files", "*.*")],
        )
        if not path_str:
            return
        path = Path(path_str)

        talismans: list[Talisman] = []
        if path.exists():
            try:
                talismans = load_talismans(path)
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("Failed to load file", f"Could not load {path}:\n{exc}")
                return

        self.custom_talisman_path = path
        self.custom_talismans = talismans
        self.ct_file_var.set(str(path))
        self._refresh_custom_talisman_skill_options()
        self._set_custom_talisman_controls_enabled(True)
        self._refresh_custom_talisman_listbox()
        self._clear_custom_talisman_form()
        self.ct_status_var.set(f"Using {path.name} ({len(talismans)} talismans)")

    def _refresh_custom_talisman_listbox(self) -> None:
        self.ct_listbox.delete(0, tk.END)
        for talisman in self.custom_talismans:
            self.ct_listbox.insert(tk.END, talisman.name)
        self.ct_selected_index = None

    def _on_custom_talisman_select(self, _event: object) -> None:
        selection = self.ct_listbox.curselection()
        if not selection:
            return
        index = selection[0]
        self.ct_selected_index = index
        talisman = self.custom_talismans[index]

        self.ct_name_var.set(talisman.name)
        self.ct_rarity_var.set(str(talisman.rarity))

        skills = list(talisman.skills) + [None] * MAX_TALISMAN_SKILLS
        for (name_var, level_var), skill in zip(self.ct_skill_rows, skills):
            if skill is not None:
                name_var.set(skill.name)
                level_var.set(str(skill.level))
            else:
                name_var.set(NONE_OPTION)
                level_var.set("1")

        armour_sizes = _slot_sizes(talisman.decoration_slots.armour)
        armour_sizes += [0] * MAX_TALISMAN_SLOTS
        for var, size in zip(self.ct_armour_slot_vars, armour_sizes):
            var.set(str(size))

        weapon_sizes = _slot_sizes(talisman.decoration_slots.weapon)
        weapon_sizes += [0] * MAX_TALISMAN_SLOTS
        for var, size in zip(self.ct_weapon_slot_vars, weapon_sizes):
            var.set(str(size))

    def _clear_custom_talisman_form(self) -> None:
        self.ct_selected_index = None
        self.ct_listbox.selection_clear(0, tk.END)
        self.ct_name_var.set("")
        self.ct_rarity_var.set("5")
        for name_var, level_var in self.ct_skill_rows:
            name_var.set(NONE_OPTION)
            level_var.set("1")
        for var in self.ct_armour_slot_vars:
            var.set("0")
        for var in self.ct_weapon_slot_vars:
            var.set("0")

    def _save_custom_talisman(self) -> None:
        if self.custom_talisman_path is None:
            messagebox.showerror(
                "Save Talisman", "Select or create a custom talismans file first."
            )
            return

        name = self.ct_name_var.get().strip()
        if not name:
            messagebox.showerror("Save Talisman", "Enter a talisman name.")
            return

        try:
            rarity = int(self.ct_rarity_var.get())
        except ValueError:
            messagebox.showerror("Save Talisman", "Rarity must be an integer.")
            return

        skills: list[SkillLevel] = []
        for name_var, level_var in self.ct_skill_rows:
            skill_name = name_var.get()
            if not skill_name or skill_name == NONE_OPTION:
                continue
            try:
                level = int(level_var.get())
            except ValueError:
                messagebox.showerror(
                    "Save Talisman", f"'{skill_name}' has an invalid level."
                )
                return
            skill = self.skills_by_name.get(skill_name)
            if skill is not None and level > skill.max_level:
                messagebox.showerror(
                    "Save Talisman",
                    f"'{skill_name}' only goes up to level {skill.max_level}.",
                )
                return
            skills.append(SkillLevel(name=skill_name, level=level))

        if not skills:
            messagebox.showerror("Save Talisman", "Select at least one skill.")
            return

        armour_slots = [int(v.get()) for v in self.ct_armour_slot_vars if int(v.get())]
        weapon_slots = [int(v.get()) for v in self.ct_weapon_slot_vars if int(v.get())]

        duplicate_index = next(
            (i for i, t in enumerate(self.custom_talismans) if t.name == name), None
        )
        if duplicate_index is not None and duplicate_index != self.ct_selected_index:
            messagebox.showerror(
                "Save Talisman", f"A talisman named '{name}' already exists."
            )
            return

        talisman = Talisman(
            name=name,
            rarity=rarity,
            skills=skills,
            slots=[],
            decoration_slots=DecorationSlots(armour=armour_slots, weapon=weapon_slots),
            source_url="custom",
        )

        if self.ct_selected_index is not None:
            self.custom_talismans[self.ct_selected_index] = talisman
        else:
            self.custom_talismans.append(talisman)

        self._write_custom_talismans(self.custom_talisman_path)
        self._refresh_custom_talisman_listbox()
        self._clear_custom_talisman_form()
        self.ct_status_var.set(f"Saved '{name}' to {self.custom_talisman_path.name}")

    def _delete_custom_talisman(self) -> None:
        if self.ct_selected_index is None:
            return
        removed = self.custom_talismans.pop(self.ct_selected_index)
        self._write_custom_talismans(self.custom_talisman_path)
        self._refresh_custom_talisman_listbox()
        self._clear_custom_talisman_form()
        self.ct_status_var.set(f"Deleted '{removed.name}'")

    def _write_custom_talismans(self, path: Path) -> None:
        output = []
        for talisman in self.custom_talismans:
            output.append(
                {
                    "name": talisman.name,
                    "rarity": talisman.rarity,
                    "skills": [
                        {"name": s.name, "level": s.level} for s in talisman.skills
                    ],
                    "slots": list(talisman.slots),
                    "decoration_slots": {
                        "armour": talisman.decoration_slots.armour,
                        "weapon": talisman.decoration_slots.weapon,
                    },
                    "source_url": talisman.source_url,
                }
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            yaml.dump(output, f, sort_keys=False, allow_unicode=True, default_flow_style=False)

    # --- optimiser integration ------------------------------------------

    def _run_optimiser(self) -> None:
        if self._optimiser_running:
            return

        try:
            reserved_slots = int(self.reserved_slots_var.get())
            if reserved_slots < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror(
                "Run Optimiser", "Reserved level-1 slots must be a non-negative integer."
            )
            return

        extra_bonus_pieces: dict[str, int] = {}
        set_bonus = self.gogma_set_var.get()
        if set_bonus and set_bonus != NONE_OPTION:
            extra_bonus_pieces[set_bonus] = extra_bonus_pieces.get(set_bonus, 0) + 1
        group_skill = self.gogma_group_var.get()
        if group_skill and group_skill != NONE_OPTION:
            extra_bonus_pieces[group_skill] = extra_bonus_pieces.get(group_skill, 0) + 1

        self._optimiser_running = True
        self.run_button.config(state=tk.DISABLED, text="Working...")
        self.status_var.set("Running optimiser - this can take up to a minute...")

        # Tkinter is not thread-safe: the worker only ever writes to this queue,
        # never touches self.root, and the main thread polls it via after().
        self._optimiser_queue: queue.Queue = queue.Queue()
        skills = self._effective_skills()
        custom_talismans = list(self.custom_talismans_loaded)
        thread = threading.Thread(
            target=self._optimiser_thread,
            args=(
                skills,
                reserved_slots,
                extra_bonus_pieces,
                custom_talismans,
                self._optimiser_queue,
            ),
            daemon=True,
        )
        thread.start()
        self.root.after(100, self._poll_optimiser_queue)

    def _optimiser_thread(
        self,
        skills: list[Skill],
        reserved_slots: int,
        extra_bonus_pieces: dict[str, int],
        custom_talismans: list[Talisman],
        result_queue: queue.Queue,
    ) -> None:
        try:
            if self.game_data is None:
                self.game_data = load_game_data()
            game_data = self.game_data
            if custom_talismans:
                game_data = replace(
                    game_data, talismans=list(game_data.talismans) + custom_talismans
                )
            scoring = Scoring(skills)
            sets, _constraint_level, _optimiser = optimise(
                game_data,
                scoring,
                reserved_slots=reserved_slots,
                extra_bonus_pieces=extra_bonus_pieces,
            )
        except Exception as exc:  # noqa: BLE001
            result_queue.put(("error", exc, None))
            return
        result_queue.put(("ok", sets, scoring))

    def _poll_optimiser_queue(self) -> None:
        try:
            kind, first, second = self._optimiser_queue.get_nowait()
        except queue.Empty:
            self.root.after(100, self._poll_optimiser_queue)
            return

        if kind == "error":
            self._optimiser_done(None, None, first)
        else:
            self._optimiser_done(first, second, None)

    def _optimiser_done(
        self,
        sets: list[GearSet] | None,
        scoring: Scoring | None,
        error: Exception | None,
    ) -> None:
        self._optimiser_running = False
        self.run_button.config(state=tk.NORMAL, text="Run Optimiser")

        if error is not None:
            self.status_var.set("")
            messagebox.showerror("Optimiser failed", str(error))
            return

        if not sets:
            self.status_var.set("")
            messagebox.showinfo(
                "Optimiser", "No gear sets could be built for this skill weighting."
            )
            return

        self.status_var.set(f"Optimiser found {len(sets)} gear sets.")
        self.gear_sets = sets
        self.gear_scoring = scoring
        self.gear_set_index = 0
        self._show_results_window()

    def _show_results_window(self) -> None:
        if self.results_window is None or not self.results_window.winfo_exists():
            window = tk.Toplevel(self.root)
            window.title("Optimiser Results")
            window.geometry("780x640")
            window.protocol("WM_DELETE_WINDOW", self._close_results_window)
            self.results_window = window

            nav = ttk.Frame(window, padding=8)
            nav.pack(side=tk.TOP, fill=tk.X)
            self.prev_button = ttk.Button(
                nav, text="< Previous", command=self._show_prev_set
            )
            self.prev_button.pack(side=tk.LEFT)
            self.set_position_var = tk.StringVar()
            ttk.Label(nav, textvariable=self.set_position_var, anchor=tk.CENTER).pack(
                side=tk.LEFT, fill=tk.X, expand=True
            )
            self.next_button = ttk.Button(nav, text="Next >", command=self._show_next_set)
            self.next_button.pack(side=tk.RIGHT)

            text_frame = ttk.Frame(window)
            text_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
            self.results_text = tk.Text(
                text_frame, wrap=tk.NONE, font=("Consolas", 10), state=tk.DISABLED
            )
            self.results_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            yscroll = ttk.Scrollbar(
                text_frame, orient=tk.VERTICAL, command=self.results_text.yview
            )
            yscroll.pack(side=tk.RIGHT, fill=tk.Y)
            self.results_text.config(yscrollcommand=yscroll.set)
        else:
            self.results_window.deiconify()
            self.results_window.lift()

        self._render_current_set()

    def _close_results_window(self) -> None:
        if self.results_window is not None:
            self.results_window.destroy()
        self.results_window = None

    def _render_current_set(self) -> None:
        total = len(self.gear_sets)
        if total == 0 or self.gear_scoring is None:
            return
        gear_set = self.gear_sets[self.gear_set_index]
        text = render_set_inline(
            gear_set, self.gear_set_index + 1, total, self.gear_scoring
        )

        self.results_text.config(state=tk.NORMAL)
        self.results_text.delete("1.0", tk.END)
        self.results_text.insert("1.0", text)
        self.results_text.config(state=tk.DISABLED)

        self.set_position_var.set(f"Set {self.gear_set_index + 1} of {total}")
        self.prev_button.config(
            state=tk.NORMAL if self.gear_set_index > 0 else tk.DISABLED
        )
        self.next_button.config(
            state=tk.NORMAL if self.gear_set_index < total - 1 else tk.DISABLED
        )

    def _show_prev_set(self) -> None:
        if self.gear_set_index > 0:
            self.gear_set_index -= 1
            self._render_current_set()

    def _show_next_set(self) -> None:
        if self.gear_set_index < len(self.gear_sets) - 1:
            self.gear_set_index += 1
            self._render_current_set()


def main() -> None:
    root = tk.Tk()
    SkillsGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
