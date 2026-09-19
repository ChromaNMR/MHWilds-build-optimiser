"""Loads the project's MHWilds data YAML files into typed data objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

SKILLS_PATH = Path(r"c:\Users\rando\Documents\VSCode projects\mhwilds optimiser\skills_default.yaml")
ARMOR_PATH = Path(r"c:\Users\rando\Documents\VSCode projects\mhwilds optimiser\high_rank_armor.yaml")
TALISMANS_PATH = Path(r"c:\Users\rando\Documents\VSCode projects\mhwilds optimiser\craftable_talismans.yaml")
DECORATIONS_PATH = Path(r"c:\Users\rando\Documents\VSCode projects\mhwilds optimiser\decorations.yaml")


@dataclass
class Skill:
    name: str
    type: str
    description: str
    max_level: int
    scaling: str
    weight: float
    level_weight: float
    source_url: str


@dataclass
class SkillLevel:
    name: str
    level: int


@dataclass
class Defense:
    base: int
    max: int


@dataclass
class Resistances:
    fire: int = 0
    water: int = 0
    thunder: int = 0
    ice: int = 0
    dragon: int = 0


@dataclass
class SetBonusEffect:
    skill: str
    pieces_required: int


@dataclass
class SetBonus:
    name: str
    type: str
    effects: list[SetBonusEffect]


@dataclass
class ArmorPiece:
    name: str
    set: str
    rarity: int
    defense: Defense
    resistances: Resistances
    skills: list[SkillLevel]
    slots: list[int]
    slots_source: str
    set_bonuses: list[SetBonus]
    source_url: str


@dataclass
class Talisman:
    name: str
    rarity: int
    skills: list[SkillLevel]
    slots: list[int]
    source_url: str


@dataclass
class Decoration:
    name: str
    type: str
    rarity: int
    slot_level: int
    skills: list[SkillLevel]
    source_url: str


@dataclass
class GameData:
    skills: list[Skill] = field(default_factory=list)
    armor: list[ArmorPiece] = field(default_factory=list)
    talismans: list[Talisman] = field(default_factory=list)
    decorations: list[Decoration] = field(default_factory=list)


def _load_yaml(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _skill_levels(raw: list[dict]) -> list[SkillLevel]:
    return [SkillLevel(name=s["name"], level=s["level"]) for s in raw]


def load_skills(path: Path = SKILLS_PATH) -> list[Skill]:
    return [Skill(**raw) for raw in _load_yaml(path)]


def load_armor() -> list[ArmorPiece]:
    pieces = []
    for raw in _load_yaml(ARMOR_PATH):
        pieces.append(
            ArmorPiece(
                name=raw["name"],
                set=raw["set"],
                rarity=raw["rarity"],
                defense=Defense(**raw["defense"]),
                resistances=Resistances(**raw["resistances"]),
                skills=_skill_levels(raw["skills"]),
                slots=raw["slots"],
                slots_source=raw["slots_source"],
                set_bonuses=[
                    SetBonus(
                        name=sb["name"],
                        type=sb["type"],
                        effects=[SetBonusEffect(**e) for e in sb["effects"]],
                    )
                    for sb in raw.get("set_bonuses", [])
                ],
                source_url=raw["source_url"],
            )
        )
    return pieces


def load_talismans() -> list[Talisman]:
    talismans = []
    for raw in _load_yaml(TALISMANS_PATH):
        talismans.append(
            Talisman(
                name=raw["name"],
                rarity=raw["rarity"],
                skills=_skill_levels(raw["skills"]),
                slots=raw["slots"],
                source_url=raw["source_url"],
            )
        )
    return talismans


def load_decorations() -> list[Decoration]:
    decorations = []
    for raw in _load_yaml(DECORATIONS_PATH):
        decorations.append(
            Decoration(
                name=raw["name"],
                type=raw["type"],
                rarity=raw["rarity"],
                slot_level=raw["slot_level"],
                skills=_skill_levels(raw["skills"]),
                source_url=raw["source_url"],
            )
        )
    return decorations


def load_game_data() -> GameData:
    return GameData(
        skills=load_skills(),
        armor=load_armor(),
        talismans=load_talismans(),
        decorations=load_decorations(),
    )


if __name__ == "__main__":
    data = load_game_data()
    print(f"Skills:      {len(data.skills)}")
    print(f"Armor:       {len(data.armor)}")
    print(f"Talismans:   {len(data.talismans)}")
    print(f"Decorations: {len(data.decorations)}")
