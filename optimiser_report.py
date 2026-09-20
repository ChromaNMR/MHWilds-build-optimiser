"""Rendering for optimiser results.

Kept separate from optimiser.py so a GUI can consume the same GearSet objects
and replace only this layer.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from optimiser import GearSet, Scoring, SlotAssignment

CONSTRAINT_DESCRIPTIONS = {
    0: "all mandatory skills present, and mandatory max-level skills at max",
    1: "all mandatory skills present (max-level requirement could not be met)",
    2: "no mandatory skill requirement could be met - ranked on score alone",
}

WIDTH = 78


def _skill_note(name: str, level: int, scoring: Scoring) -> str:
    skill = scoring.by_name.get(name)
    if skill is None:
        return ""
    notes = []
    if level >= skill.max_level:
        notes.append("MAX")
    if name in scoring.mandatory_max:
        notes.append("mandatory-max")
    elif name in scoring.mandatory:
        notes.append("mandatory")
    if skill.weight:
        notes.append(f"w{skill.weight:g}/lw{skill.level_weight:g}")
    return "  " + " ".join(f"[{n}]" for n in notes) if notes else ""


def _sorted_skills(gear_set: GearSet, scoring: Scoring):
    def sort_key(item):
        name, level = item
        skill = scoring.by_name.get(name)
        weight = skill.weight if skill else 0.0
        return (-weight, -level, name)

    return sorted(gear_set.skill_levels.items(), key=sort_key)


def _bonus_pieces_text(bonus) -> str:
    text = f"{bonus.pieces} pieces"
    if bonus.extra:
        text += f" (+{bonus.extra} weapon)"
    return text


def _decorations_by_source(gear_set: GearSet) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for placement in gear_set.placements:
        if placement.decoration is None:
            continue
        grouped.setdefault(placement.source, []).append(placement.decoration.name)
    return grouped


def render_set(gear_set: GearSet, rank: int, scoring: Scoring) -> str:
    lines = ["=" * WIDTH]
    tier = f"  [{gear_set.tier}]" if gear_set.tier else ""
    lines.append(
        f"Set {rank}{tier}   score {gear_set.total_score:.2f}"
        f"   (skills {gear_set.skill_score:.2f} + defence {gear_set.defense_score:.2f})"
    )
    lines.append("-" * WIDTH)

    for piece in gear_set.pieces:
        slots = ", ".join(str(s) for s in piece.slots if s) or "-"
        lines.append(
            f"  {piece.piece_type:<6} {piece.name:<26} {piece.set:<18}"
            f" def {piece.defense.max:>3}  slots [{slots}]"
        )
    talisman_skills = ", ".join(
        f"{s.name} {s.level}" for s in gear_set.talisman.skills
    )
    lines.append(f"  {'charm':<6} {gear_set.talisman.name:<26} {talisman_skills}")
    lines.append(
        f"  Total defence {gear_set.defense_total}"
        + (
            f"   |   weapon slots on charm: {gear_set.weapon_slots}"
            if gear_set.weapon_slots
            else ""
        )
    )

    lines.append("")
    lines.append("  Skills:")
    for name, level in _sorted_skills(gear_set, scoring):
        skill = scoring.by_name.get(name)
        if skill is None:
            continue
        if skill.type in ("Set Bonus", "Group"):
            continue
        capped = min(level, skill.max_level)
        lines.append(
            f"    {name:<28} {capped}/{skill.max_level}{_skill_note(name, capped, scoring)}"
        )

    if gear_set.active_bonuses:
        lines.append("")
        lines.append("  Set / group bonuses:")
        for bonus in gear_set.active_bonuses:
            kind = "group" if bonus.bonus_type == "group_skill" else "set"
            effects = ", ".join(bonus.effects)
            weight = scoring.weight(bonus.name)
            marker = f"  [w{weight:g}]" if weight else ""
            lines.append(
                f"    {bonus.name:<28} {kind:<5} {_bonus_pieces_text(bonus)}"
                f" -> level {bonus.level}  ({effects}){marker}"
            )

    grouped = _decorations_by_source(gear_set)
    lines.append("")
    if grouped:
        lines.append("  Decorations:")
        for source, decos in grouped.items():
            lines.append(f"    {source:<28} {', '.join(decos)}")
    else:
        lines.append("  Decorations: none worth slotting")

    if gear_set.free_slots:
        sizes = ", ".join(str(s) for s in gear_set.free_slots)
        reserved = min(gear_set.reserved_slots, len(gear_set.free_slots))
        note = (
            f" ({reserved} reserved for resistance jewels)"
            if reserved
            else " (nothing worth slotting)"
        )
        lines.append(f"  Free slots: [{sizes}]{note}")
    else:
        lines.append("  Free slots: none")

    return "\n".join(lines)


def _placements_by_source(gear_set: GearSet) -> dict[str, list[SlotAssignment]]:
    """Every slot (filled or not) grouped by the piece/talisman it belongs to."""
    grouped: dict[str, list[SlotAssignment]] = {}
    for placement in gear_set.placements:
        grouped.setdefault(placement.source, []).append(placement)
    return grouped


def _slot_brackets(placements: list[SlotAssignment]) -> str:
    if not placements:
        return ""
    parts = [
        f"[{p.size}: {p.decoration.name if p.decoration else 'empty'}]"
        for p in placements
    ]
    return "  " + " ".join(parts)


def render_set_inline(gear_set: GearSet, rank: int, total: int, scoring: Scoring) -> str:
    """Render a set with each piece's decoration slots inline on its own line.

    Used by the GUI results viewer (one set at a time); the console renderer
    (render_set) instead groups decorations into a separate section.
    """
    lines = []
    tier = f"  [{gear_set.tier}]" if gear_set.tier else ""
    lines.append(
        f"Set {rank} of {total}{tier}   score {gear_set.total_score:.2f}"
        f"   (skills {gear_set.skill_score:.2f} + defence {gear_set.defense_score:.2f})"
    )
    lines.append("-" * WIDTH)

    by_source = _placements_by_source(gear_set)
    for piece in gear_set.pieces:
        brackets = _slot_brackets(by_source.get(piece.name, []))
        lines.append(
            f"  {piece.piece_type:<6} {piece.name:<26} {piece.set:<18}"
            f" def {piece.defense.max:>3}{brackets}"
        )

    talisman_skills = ", ".join(
        f"{s.name} {s.level}" for s in gear_set.talisman.skills
    )
    talisman_brackets = _slot_brackets(by_source.get(gear_set.talisman.name, []))
    lines.append(
        f"  {'charm':<6} {gear_set.talisman.name:<26} {talisman_skills}{talisman_brackets}"
    )
    lines.append(
        f"  Total defence {gear_set.defense_total}"
        + (
            f"   |   weapon slots on charm: {gear_set.weapon_slots}"
            if gear_set.weapon_slots
            else ""
        )
    )

    lines.append("")
    lines.append("  Skills:")
    for name, level in _sorted_skills(gear_set, scoring):
        skill = scoring.by_name.get(name)
        if skill is None or skill.type in ("Set Bonus", "Group"):
            continue
        capped = min(level, skill.max_level)
        lines.append(
            f"    {name:<28} {capped}/{skill.max_level}{_skill_note(name, capped, scoring)}"
        )

    if gear_set.active_bonuses:
        lines.append("")
        lines.append("  Set / group bonuses:")
        for bonus in gear_set.active_bonuses:
            kind = "group" if bonus.bonus_type == "group_skill" else "set"
            effects = ", ".join(bonus.effects)
            weight = scoring.weight(bonus.name)
            marker = f"  [w{weight:g}]" if weight else ""
            lines.append(
                f"    {bonus.name:<28} {kind:<5} {_bonus_pieces_text(bonus)}"
                f" -> level {bonus.level}  ({effects}){marker}"
            )

    lines.append("")
    if gear_set.free_slots:
        sizes = ", ".join(str(s) for s in gear_set.free_slots)
        reserved = min(gear_set.reserved_slots, len(gear_set.free_slots))
        note = (
            f" ({reserved} reserved for resistance jewels)"
            if reserved
            else " (nothing worth slotting)"
        )
        lines.append(f"  Free slots: [{sizes}]{note}")
    else:
        lines.append("  Free slots: none")

    return "\n".join(lines)


def render_console(
    sets: list[GearSet], scoring: Scoring, constraint_level: int, db_path: Path
) -> str:
    header = [
        "=" * WIDTH,
        f"MH Wilds gear sets for {db_path}",
        f"Constraint tier {constraint_level}: {CONSTRAINT_DESCRIPTIONS[constraint_level]}",
    ]
    mandatory = sorted(scoring.mandatory)
    if mandatory:
        header.append("Mandatory skills: " + ", ".join(mandatory))
    if constraint_level > 0:
        header.append(
            "NOTE: constraints were relaxed to fill the requested number of sets."
        )

    if not sets:
        header.append("")
        header.append("No gear sets could be built.")
        return "\n".join(header)

    body = [render_set(s, i, scoring) for i, s in enumerate(sets, start=1)]
    return "\n".join(header) + "\n" + "\n".join(body)


def gear_set_to_dict(gear_set: GearSet, rank: int, scoring: Scoring) -> dict:
    return {
        "rank": rank,
        "tier": gear_set.tier,
        "score": {
            "total": round(gear_set.total_score, 3),
            "skills": round(gear_set.skill_score, 3),
            "defence": round(gear_set.defense_score, 3),
        },
        "defence_total": gear_set.defense_total,
        "pieces": [
            {
                "piece_type": p.piece_type,
                "name": p.name,
                "set": p.set,
                "defence": p.defense.max,
                "slots": [s for s in p.slots if s],
            }
            for p in gear_set.pieces
        ],
        "talisman": {
            "name": gear_set.talisman.name,
            "skills": [
                {"name": s.name, "level": s.level} for s in gear_set.talisman.skills
            ],
        },
        "skills": {
            name: min(level, scoring.by_name[name].max_level)
            for name, level in _sorted_skills(gear_set, scoring)
            if name in scoring.by_name
        },
        "set_bonuses": [
            {
                "name": b.name,
                "type": b.bonus_type,
                "pieces": b.pieces,
                "extra_from_weapon": b.extra,
                "level": b.level,
                "effects": list(b.effects),
            }
            for b in gear_set.active_bonuses
        ],
        "decorations": [
            {
                "source": p.source,
                "slot_size": p.size,
                "decoration": p.decoration.name,
            }
            for p in gear_set.placements
            if p.decoration is not None
        ],
        "free_slots": gear_set.free_slots,
        "reserved_slots": gear_set.reserved_slots,
        "weapon_slots": gear_set.weapon_slots,
    }


def write_yaml(
    sets: list[GearSet],
    scoring: Scoring,
    constraint_level: int,
    db_path: Path,
    output: Path,
) -> None:
    payload = {
        "skills_db": str(db_path),
        "constraint_level": constraint_level,
        "constraint": CONSTRAINT_DESCRIPTIONS[constraint_level],
        "sets": [
            gear_set_to_dict(s, i, scoring) for i, s in enumerate(sets, start=1)
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as f:
        yaml.dump(payload, f, sort_keys=False, allow_unicode=True, default_flow_style=False)
