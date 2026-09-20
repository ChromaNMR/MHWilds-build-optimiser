"""Simple GUI for assigning weight / level_weight to skills.

Filter skills by type, edit their weight and level_weight, then save a
copy of skills.yaml (with the edited values) as skills_weighted.yaml.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from dataclasses import asdict, replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import yaml

from load_data import SKILLS_PATH, GameData, Skill, load_game_data, load_skills
from optimiser import GearSet, Scoring, optimise
from optimiser_report import render_set_inline


class SkillsGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("MHWilds Skill Weights")
        self.root.geometry("700x560")

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

        self._build_widgets()
        self._load_file(self.current_path)

    def _build_widgets(self) -> None:
        file_row = ttk.Frame(self.root, padding=(8, 8, 8, 0))
        file_row.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(file_row, text="Open...", command=self._open_file).pack(side=tk.LEFT)
        self.file_label_var = tk.StringVar(value="")
        ttk.Label(file_row, textvariable=self.file_label_var, foreground="#555555").pack(
            side=tk.LEFT, padx=(8, 0)
        )

        top = ttk.Frame(self.root, padding=8)
        top.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(top, text="Type:").pack(side=tk.LEFT)
        self.type_var = tk.StringVar(value="All")
        self.type_combo = ttk.Combobox(
            top, textvariable=self.type_var, values=["All"], state="readonly", width=15
        )
        self.type_combo.pack(side=tk.LEFT, padx=(4, 0))
        self.type_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_listbox())

        body = ttk.Frame(self.root, padding=8)
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
        bottom = ttk.Frame(self.root, padding=8)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_var = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.status_var, foreground="#2a7f2a").pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )
        self.run_button = ttk.Button(
            bottom, text="Run Optimiser", command=self._run_optimiser
        )
        self.run_button.pack(side=tk.RIGHT)

        output_row = ttk.Frame(self.root, padding=(8, 0, 8, 4))
        output_row.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(output_row, text="Output file:").pack(side=tk.LEFT)
        self.output_name_var = tk.StringVar(value="skills_weighted.yaml")
        ttk.Entry(output_row, textvariable=self.output_name_var, width=28).pack(
            side=tk.LEFT, padx=(4, 4)
        )
        ttk.Button(output_row, text="Browse...", command=self._browse_save).pack(side=tk.LEFT)
        ttk.Button(output_row, text="Save", command=self._save).pack(side=tk.LEFT, padx=(4, 0))

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

        self._refresh_listbox()

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

    # --- optimiser integration ------------------------------------------

    def _run_optimiser(self) -> None:
        if self._optimiser_running:
            return
        self._optimiser_running = True
        self.run_button.config(state=tk.DISABLED, text="Working...")
        self.status_var.set("Running optimiser - this can take up to a minute...")

        # Tkinter is not thread-safe: the worker only ever writes to this queue,
        # never touches self.root, and the main thread polls it via after().
        self._optimiser_queue: queue.Queue = queue.Queue()
        skills = self._effective_skills()
        thread = threading.Thread(
            target=self._optimiser_thread,
            args=(skills, self._optimiser_queue),
            daemon=True,
        )
        thread.start()
        self.root.after(100, self._poll_optimiser_queue)

    def _optimiser_thread(self, skills: list[Skill], result_queue: queue.Queue) -> None:
        try:
            if self.game_data is None:
                self.game_data = load_game_data()
            scoring = Scoring(skills)
            sets, _constraint_level, _optimiser = optimise(self.game_data, scoring)
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
