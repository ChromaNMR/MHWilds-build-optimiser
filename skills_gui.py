"""Simple GUI for assigning weight / level_weight to skills.

Filter skills by type, edit their weight and level_weight, then save a
copy of skills.yaml (with the edited values) as skills_weighted.yaml.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import asdict
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import yaml

from load_data import SKILLS_PATH, Skill, load_skills


class SkillsGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("MHWilds Skill Weights")
        self.root.geometry("700x520")

        self.current_path: Path = Path(SKILLS_PATH)
        self.skills: list[Skill] = []
        self.skills_by_name: dict[str, Skill] = {}
        self.cache: dict[str, dict[str, str]] = {}

        self.selected_name: str | None = None
        self._suppress_trace = False

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

        bottom = ttk.Frame(self.root, padding=8)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_var = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.status_var, foreground="#2a7f2a").pack(side=tk.LEFT)
        ttk.Button(bottom, text="Save As...", command=self._save).pack(side=tk.RIGHT)

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

    def _save(self) -> None:
        path_str = filedialog.asksaveasfilename(
            title="Save weighted skills as",
            initialdir=str(self.current_path.parent),
            initialfile="skills_weighted.yaml",
            defaultextension=".yaml",
            filetypes=[("YAML files", "*.yaml *.yml"), ("All files", "*.*")],
        )
        if not path_str:
            return
        output_path = Path(path_str)

        output = []
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

            data = asdict(skill)
            data["weight"] = weight
            data["level_weight"] = level_weight
            output.append(data)

        with output_path.open("w", encoding="utf-8") as f:
            yaml.dump(output, f, sort_keys=False, allow_unicode=True, default_flow_style=False)

        self.status_var.set(f"Saved to {output_path.name}")
        messagebox.showinfo("Saved", f"Saved weights to {output_path}")


def main() -> None:
    root = tk.Tk()
    SkillsGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
