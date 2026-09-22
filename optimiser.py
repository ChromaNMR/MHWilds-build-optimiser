"""Gear set optimiser for Monster Hunter Wilds.

Reads a weighted skills DB (each skill carrying a 'weight' and 'level_weight')
and searches for gear sets: one armour piece per equipment slot, one talisman,
and decorations placed only where they raise a weighted skill.

This module is deliberately free of printing so a GUI can import and call
``optimise()`` directly. Rendering lives in optimiser_report.py.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path

from load_data import (
    ArmorPiece,
    Decoration,
    GameData,
    Skill,
    Talisman,
    load_game_data,
    load_skills,
)

# --- tuning constants -------------------------------------------------------

LEVEL_CURVE = 1.0  # >1 punishes partial levels harder for high level_weight

# How much more a high-weight skill matters than a low-weight one. At 1.0 a
# weight-2 skill sitting at level 1 outscores a further level of a weight-4
# skill, which fills sets with shallow filler; raising it buys depth in the
# skills that matter over breadth of cheap level-1 skills. Above ~2.5 the gap
# gets extreme enough that mid-weight skills start losing their depth again.
WEIGHT_EXPONENT = 2.0

# How sharply level_weight drives the value of each extra level. The share of a
# skill's value that sits in its levels is (level_weight / 5) ** this. Holding it
# just above WEIGHT_EXPONENT is what makes a point in a 1/4 skill beat a point in
# a 4/1 one while keeping 2/3 and 3/2 roughly even; at exactly WEIGHT_EXPONENT
# those first two come out equal instead.
LEVEL_WEIGHT_EXPONENT = 2.2

# Whole-set defence is worth about as much as one skill of this weight. It is
# raised to WEIGHT_EXPONENT alongside skills so the balance holds if that moves.
DEFENSE_EQUIV_WEIGHT = 2.0
DEFENSE_EXPONENT = 2.5  # >1 makes low-defence pieces substantially worse
DEFENSE_FLOOR = 62
DEFENSE_CEILING = 94

MANDATORY_WEIGHT = 5.0  # weight at which a skill is treated as mandatory
MAX_LEVEL_WEIGHT = 5.0  # level_weight at which a skill must reach max level

RESERVED_SLOTS = 2  # slots held back for per-hunt resistance jewels
# 3000/1200 left ~1% of score on the table; the result plateaus here and wider
# beams (9000/15000) find nothing better, at several times the runtime.
BEAM_WIDTH = 5000
FINAL_POOL = 2000  # complete armour combos given the full evaluation
TALISMAN_SHORTLIST = 5

# The talisman schema stores a decoration slot *count* with no size, so a count
# is read as this many slots of this size. A list of sizes is also accepted.
TALISMAN_ARMOUR_SLOT_SIZE = 1

# Credit given to partial progress toward a set/group bonus threshold, so the
# beam search will hold onto states that are part-way to completing one.
BONUS_PROGRESS_CREDIT = 0.8

PIECE_TYPES = ("head", "chest", "arms", "waist", "legs")
SET_BONUS_SUFFIX = " Set Bonus"
GROUP_SKILL_SUFFIX = " Group Skill"


@dataclass(frozen=True)
class DiversityTier:
    """One band of the final result list."""

    label: str
    count: int
    min_piece_diff: int = 1
    require_new_bonuses: bool = False


DEFAULT_TIERS = (
    DiversityTier("closest variants", 3, min_piece_diff=1),
    DiversityTier("distinct builds", 3, min_piece_diff=2),
    DiversityTier("distinct bonuses", 4, min_piece_diff=1, require_new_bonuses=True),
)


# --- scoring ----------------------------------------------------------------


def defense_value(defense_max: int) -> float:
    """Normalised, non-linear worth of a piece's max defence."""
    span = DEFENSE_CEILING - DEFENSE_FLOOR
    t = (defense_max - DEFENSE_FLOOR) / span
    return max(0.0, min(1.0, t)) ** DEFENSE_EXPONENT


def bonus_base_name(name: str) -> str:
    """'Gore Magala's Tyranny Set Bonus' -> 'Gore Magala's Tyranny'."""
    for suffix in (SET_BONUS_SUFFIX, GROUP_SKILL_SUFFIX):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


class Scoring:
    """Turns achieved skill levels into a score using the weighted skills DB."""

    def __init__(
        self,
        skills: list[Skill],
        weight_exponent: float = WEIGHT_EXPONENT,
        level_weight_exponent: float = LEVEL_WEIGHT_EXPONENT,
        level_curve: float = LEVEL_CURVE,
        defense_equiv_weight: float = DEFENSE_EQUIV_WEIGHT,
    ) -> None:
        self.weight_exponent = weight_exponent
        self.level_weight_exponent = level_weight_exponent
        self.level_curve = level_curve
        self.defense_points = defense_equiv_weight**weight_exponent
        self.by_name = {s.name: s for s in skills}
        self.relevant = sorted(s.name for s in skills if s.weight != 0)
        self.mandatory = {s.name for s in skills if s.weight >= MANDATORY_WEIGHT}
        self.mandatory_max = {
            s.name
            for s in skills
            if s.weight >= MANDATORY_WEIGHT and s.level_weight >= MAX_LEVEL_WEIGHT
        }
        self._score_cache: dict[tuple[str, int], float] = {}

    def max_level(self, name: str) -> int:
        return self.by_name[name].max_level

    def weight(self, name: str) -> float:
        skill = self.by_name.get(name)
        return skill.weight if skill else 0.0

    def score(self, name: str, level: int) -> float:
        key = (name, level)
        cached = self._score_cache.get(key)
        if cached is not None:
            return cached

        skill = self.by_name.get(name)
        if skill is None or skill.weight == 0 or level <= 0:
            value = 0.0
        else:
            capped = min(level, skill.max_level)
            # Share of the skill's value carried by its levels rather than by
            # merely having it. Raising level_weight to an exponent above
            # WEIGHT_EXPONENT makes each point worth more in skills you want
            # levelled than in merely high-weight ones.
            alpha = max(0.0, min(1.0, skill.level_weight / 5.0))
            alpha **= self.level_weight_exponent
            progress = (capped / skill.max_level) ** self.level_curve
            # Sign-preserving, so 'actively avoid' weights stay negative.
            importance = math.copysign(
                abs(skill.weight) ** self.weight_exponent, skill.weight
            )
            value = importance * ((1.0 - alpha) + alpha * progress)

        self._score_cache[key] = value
        return value

    def marginal(self, name: str, level: int) -> float:
        """Value of raising a skill from ``level`` to ``level + 1``."""
        return self.score(name, level + 1) - self.score(name, level)


# --- gear profiles ----------------------------------------------------------


def slot_counts(sizes) -> tuple[int, int, int]:
    """Slot sizes -> counts of (size-1, size-2, size-3) slots."""
    counts = [0, 0, 0]
    for size in sizes:
        if 1 <= size <= 3:
            counts[size - 1] += 1
    return counts[0], counts[1], counts[2]


def talisman_slot_sizes(talisman: Talisman) -> tuple[list[int], int]:
    """(armour slot sizes, weapon slot count) for a talisman.

    Craftable talismans currently have none of either, but appraised ones are
    expected to, so both the count form and a list-of-sizes form are accepted.
    """
    deco = talisman.decoration_slots
    armour = deco.armour
    if isinstance(armour, (list, tuple)):
        sizes = [s for s in armour if s]
    else:
        sizes = [TALISMAN_ARMOUR_SLOT_SIZE] * int(armour)

    weapon = deco.weapon
    weapon_count = len([w for w in weapon if w]) if isinstance(weapon, (list, tuple)) else int(weapon)
    return sizes, weapon_count


def consume_slot(slots: tuple[int, int, int], size: int, count: int = 1):
    """Spend ``count`` slots able to hold a size-``size`` decoration.

    Always takes the smallest adequate slot, which is optimal here: slot
    capability is nested (a size-3 slot accepts anything a size-1 one does), so
    never burning a large slot on a small gem can only help later placements.
    """
    n1, n2, n3 = slots
    for _ in range(count):
        if size <= 1 and n1:
            n1 -= 1
        elif size <= 2 and n2:
            n2 -= 1
        elif n3:
            n3 -= 1
        else:
            return None
    return n1, n2, n3


@dataclass
class PieceProfile:
    """An armour piece reduced to only what the search cares about."""

    piece: ArmorPiece
    skill_levels: tuple[int, ...]  # indexed by Context.relevant_skills
    slots: tuple[int, int, int]
    defense: int
    defense_value: float
    bonus_indices: tuple[int, ...]  # indexed by Context.relevant_bonuses


@dataclass
class BonusInfo:
    name: str
    bonus_type: str
    thresholds: tuple[int, ...]
    effects: tuple[str, ...]

    def level_for(self, pieces: int) -> int:
        return sum(1 for t in self.thresholds if pieces >= t)


def resolve_pins(
    game: GameData, pinned_pieces: dict[str, str] | None
) -> dict[str, ArmorPiece]:
    """Equipment slot -> the armour piece pinned to it.

    Piece names are unique across the armour data, so a name alone identifies a
    piece. Every input is validated here rather than at the call site because an
    unrecognised name would otherwise reach the beam as an empty candidate list,
    turning a typo into a search that silently returns nothing.
    """
    if not pinned_pieces:
        return {}

    by_name = {piece.name: piece for piece in game.armor}
    resolved: dict[str, ArmorPiece] = {}
    for piece_type, name in pinned_pieces.items():
        if not name:
            continue
        if piece_type not in PIECE_TYPES:
            raise ValueError(
                f"{piece_type!r} is not an equipment slot; expected one of "
                + ", ".join(PIECE_TYPES)
            )
        piece = by_name.get(name)
        if piece is None:
            raise ValueError(f"No armour piece is named {name!r}.")
        if piece.piece_type != piece_type:
            raise ValueError(
                f"{name!r} is a {piece.piece_type} piece and cannot be pinned to "
                f"the {piece_type} slot."
            )
        resolved[piece_type] = piece
    return resolved


class Context:
    """Precomputed, weight-profile-specific view of the game data."""

    def __init__(
        self,
        game: GameData,
        scoring: Scoring,
        pinned_pieces: dict[str, str] | None = None,
    ) -> None:
        self.game = game
        self.scoring = scoring
        self.pinned = resolve_pins(game, pinned_pieces)

        self.bonus_registry = self._build_bonus_registry(game)

        self.relevant_skills = [
            name for name in scoring.relevant if name in self._gear_reachable_skills(game)
        ]
        self.skill_index = {name: i for i, name in enumerate(self.relevant_skills)}

        self.relevant_bonuses = sorted(
            name for name in self.bonus_registry if scoring.weight(name) != 0
        )
        self.bonus_index = {name: i for i, name in enumerate(self.relevant_bonuses)}

        self.decoration_for_skill = self._best_decorations(game)
        # A pinned slot skips prune_dominated entirely: pruning only decides
        # between alternatives, and a pinned slot has none. The single candidate
        # also makes the slot sort first in _search_armour's stage order, so every
        # later decision is ranked with the pinned piece already counted.
        self.candidates = {
            piece_type: (
                [self.profile(self.pinned[piece_type])]
                if piece_type in self.pinned
                else prune_dominated(
                    [self.profile(a) for a in game.armor if a.piece_type == piece_type],
                    scoring,
                    self.relevant_skills,
                )
            )
            for piece_type in PIECE_TYPES
        }

    @staticmethod
    def _build_bonus_registry(game: GameData) -> dict[str, BonusInfo]:
        registry: dict[str, BonusInfo] = {}
        for piece in game.armor:
            for bonus in piece.set_bonuses:
                base = bonus_base_name(bonus.name)
                if base not in registry:
                    registry[base] = BonusInfo(
                        name=base,
                        bonus_type=bonus.type,
                        thresholds=tuple(e.pieces_required for e in bonus.effects),
                        effects=tuple(e.skill for e in bonus.effects),
                    )
        return registry

    @staticmethod
    def _gear_reachable_skills(game: GameData) -> set[str]:
        """Skills actually obtainable from armour, talismans or armour gems."""
        reachable = {s.name for a in game.armor for s in a.skills}
        reachable |= {s.name for t in game.talismans for s in t.skills}
        reachable |= {
            s.name for d in game.decorations if d.type == "armor" for s in d.skills
        }
        return reachable

    def _best_decorations(self, game: GameData) -> dict[str, Decoration]:
        """Cheapest armour decoration granting each skill."""
        best: dict[str, Decoration] = {}
        for deco in game.decorations:
            if deco.type != "armor":
                continue
            for granted in deco.skills:
                current = best.get(granted.name)
                if current is None or deco.slot_level < current.slot_level:
                    best[granted.name] = deco
        return best

    def unreachable_weighted_skills(self) -> list[str]:
        reachable = self._gear_reachable_skills(self.game)
        return [
            name
            for name in self.scoring.relevant
            if name not in reachable and name not in self.bonus_registry
        ]

    def profile(self, piece: ArmorPiece) -> PieceProfile:
        levels = [0] * len(self.relevant_skills)
        for granted in piece.skills:
            idx = self.skill_index.get(granted.name)
            if idx is not None:
                levels[idx] += granted.level

        bonuses = []
        for bonus in piece.set_bonuses:
            idx = self.bonus_index.get(bonus_base_name(bonus.name))
            if idx is not None:
                bonuses.append(idx)

        return PieceProfile(
            piece=piece,
            skill_levels=tuple(levels),
            slots=slot_counts(piece.slots),
            defense=piece.defense.max,
            defense_value=defense_value(piece.defense.max),
            bonus_indices=tuple(sorted(bonuses)),
        )


def prune_dominated(
    profiles: list[PieceProfile], scoring: Scoring, relevant_skills: list[str]
) -> list[PieceProfile]:
    """Drop pieces that some other piece is at least as good as in every way.

    Pieces are only compared within the same relevant-bonus group, since a
    piece carrying a weighted set bonus is never interchangeable with one that
    doesn't.
    """
    wants_more = [scoring.weight(name) > 0 for name in relevant_skills]

    def cumulative(slots: tuple[int, int, int]) -> tuple[int, int, int]:
        n1, n2, n3 = slots
        return n3, n3 + n2, n3 + n2 + n1

    def dominates(a: PieceProfile, b: PieceProfile) -> bool:
        for i, more_is_better in enumerate(wants_more):
            if more_is_better:
                if a.skill_levels[i] < b.skill_levels[i]:
                    return False
            elif a.skill_levels[i] > b.skill_levels[i]:
                return False
        if any(x < y for x, y in zip(cumulative(a.slots), cumulative(b.slots))):
            return False
        return a.defense >= b.defense

    groups: dict[tuple[int, ...], list[PieceProfile]] = {}
    for profile in profiles:
        groups.setdefault(profile.bonus_indices, []).append(profile)

    kept: list[PieceProfile] = []
    for group in groups.values():
        group.sort(
            key=lambda p: (sum(p.skill_levels), sum(p.slots), p.defense), reverse=True
        )
        survivors: list[PieceProfile] = []
        for profile in group:
            if not any(dominates(other, profile) for other in survivors):
                survivors.append(profile)
        kept.extend(survivors)
    return kept


# --- decoration filling -----------------------------------------------------


@dataclass
class DecoOption:
    skill: str
    decoration: Decoration
    size: int
    values: tuple[float, ...]  # marginal value of each further level


def decoration_options(
    levels: dict[str, int], context: Context
) -> list[DecoOption]:
    scoring = context.scoring
    options: list[DecoOption] = []
    for name in context.relevant_skills:
        if scoring.weight(name) <= 0:
            continue
        deco = context.decoration_for_skill.get(name)
        if deco is None:
            continue
        current = levels.get(name, 0)
        headroom = scoring.max_level(name) - current
        if headroom <= 0:
            continue
        values = tuple(scoring.marginal(name, current + i) for i in range(headroom))
        if not any(v > 0 for v in values):
            continue
        options.append(
            DecoOption(
                skill=name, decoration=deco, size=deco.slot_level, values=values
            )
        )
    # Bigger gems first: they are the constrained resource.
    options.sort(key=lambda o: (-o.size, -o.values[0]))
    return options


def fill_slots(
    options: list[DecoOption], slots: tuple[int, int, int]
) -> tuple[float, tuple[int, ...]]:
    """Exact best decoration mix for the available slots.

    Returns (added score, count of each option taken). Ties prefer taking
    nothing, so slots are never filled with gems that add no value.
    """
    memo: dict[tuple[int, tuple[int, int, int]], tuple[float, tuple[int, ...]]] = {}

    def solve(idx: int, free: tuple[int, int, int]):
        if idx == len(options):
            return 0.0, ()
        key = (idx, free)
        cached = memo.get(key)
        if cached is not None:
            return cached

        skip_value, skip_counts = solve(idx + 1, free)
        best = (skip_value, (0,) + skip_counts)

        option = options[idx]
        remaining = free
        gained = 0.0
        for taken in range(1, len(option.values) + 1):
            remaining = consume_slot(remaining, option.size)
            if remaining is None:
                break
            gained += option.values[taken - 1]
            sub_value, sub_counts = solve(idx + 1, remaining)
            if gained + sub_value > best[0]:
                best = (gained + sub_value, (taken,) + sub_counts)

        memo[key] = best
        return best

    return solve(0, slots)


# --- results ----------------------------------------------------------------


@dataclass
class SlotAssignment:
    source: str  # armour piece or talisman the slot belongs to
    size: int
    decoration: Decoration | None = None


@dataclass
class ActiveBonus:
    name: str
    bonus_type: str
    pieces: int  # from armour only
    extra: int  # from a weapon-granted bonus point, if any (see extra_bonus_pieces)
    level: int
    effects: list[str]


@dataclass
class GearSet:
    pieces: list[ArmorPiece]
    talisman: Talisman
    skill_levels: dict[str, int]
    active_bonuses: list[ActiveBonus]
    placements: list[SlotAssignment]
    free_slots: list[int]
    reserved_slots: int
    weapon_slots: int
    defense_total: int
    skill_score: float
    defense_score: float
    total_score: float
    constraint_level: int
    tier: str = ""
    pinned_types: frozenset[str] = frozenset()

    @property
    def piece_names(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.pieces)

    @property
    def bonus_signature(self) -> frozenset[tuple[str, int]]:
        return frozenset((b.name, b.level) for b in self.active_bonuses)


def constraint_level_met(levels: dict[str, int], scoring: Scoring) -> int:
    """0 = mandatory maxed, 1 = mandatory present, 2 = nothing enforced."""
    for name in scoring.mandatory:
        if levels.get(name, 0) < 1:
            return 2
    for name in scoring.mandatory_max:
        if levels.get(name, 0) < scoring.max_level(name):
            return 1
    return 0


# --- search -----------------------------------------------------------------


@dataclass
class SearchState:
    profiles: tuple[PieceProfile, ...]
    levels: tuple[int, ...]
    slots: tuple[int, int, int]
    defense_value_sum: float
    bonus_counts: tuple[int, ...]
    rank: float


class Optimiser:
    def __init__(
        self,
        game: GameData,
        scoring: Scoring,
        beam_width: int = BEAM_WIDTH,
        final_pool: int = FINAL_POOL,
        reserved_slots: int = RESERVED_SLOTS,
        extra_bonus_pieces: dict[str, int] | None = None,
        pinned_pieces: dict[str, str] | None = None,
    ) -> None:
        self.game = game
        self.scoring = scoring
        self.context = Context(game, scoring, pinned_pieces=pinned_pieces)
        self.beam_width = beam_width
        self.final_pool = final_pool
        self.reserved_slots = reserved_slots
        # Piece-count-equivalents credited to a named set/group bonus from a
        # source outside armour (e.g. a weapon's own bonus point), keyed by
        # the bonus's base name (see bonus_base_name). See the GUI's "Gogma
        # weapon skills" selectors.
        self.extra_bonus_pieces = {k: v for k, v in (extra_bonus_pieces or {}).items() if v}
        self._slot_potential = self._build_slot_potential()
        self._talisman_levels = self._best_talisman_levels()

    def _build_slot_potential(self) -> tuple[float, float, float]:
        """Optimistic value of one free slot of each size, for beam ranking."""
        best = [0.0, 0.0, 0.0]
        for name in self.context.relevant_skills:
            weight = self.scoring.weight(name)
            if weight <= 0:
                continue
            deco = self.context.decoration_for_skill.get(name)
            if deco is None:
                continue
            value = self.scoring.marginal(name, 0)
            for size in range(deco.slot_level, 4):
                best[size - 1] = max(best[size - 1], value)
        return best[0], best[1], best[2]

    def _best_talisman_levels(self) -> dict[str, int]:
        best: dict[str, int] = {}
        for talisman in self.game.talismans:
            for granted in talisman.skills:
                if granted.level > best.get(granted.name, 0):
                    best[granted.name] = granted.level
        return best

    def _mandatory_reachable(self, state: SearchState) -> bool:
        """Could this complete armour set still satisfy the mandatory skills?

        Checks that the gem slots (optionally helped by the best talisman for
        one skill) can cover the remaining levels. Used to prioritise the final
        pool, so a conservative answer only costs ranking, not correctness.
        """
        context = self.context
        scoring = self.scoring

        deficits: dict[str, int] = {}
        for name in scoring.mandatory:
            target = (
                scoring.max_level(name) if name in scoring.mandatory_max else 1
            )
            idx = context.skill_index.get(name)
            if idx is None:
                bonus_idx = context.bonus_index.get(name)
                if bonus_idx is None:
                    continue
                info = context.bonus_registry[name]
                have = state.bonus_counts[bonus_idx] + self.extra_bonus_pieces.get(name, 0)
                if info.level_for(have) < target:
                    return False
                continue
            shortfall = target - state.levels[idx]
            if shortfall > 0:
                deficits[name] = shortfall

        if not deficits:
            return True

        talisman_levels = self._talisman_levels
        # A talisman can cover one skill; try each in turn.
        for helped in [None, *deficits]:
            needs: list[int] = []
            feasible = True
            for name, shortfall in deficits.items():
                if name == helped:
                    shortfall -= talisman_levels.get(name, 0)
                if shortfall <= 0:
                    continue
                deco = context.decoration_for_skill.get(name)
                if deco is None:
                    feasible = False
                    break
                needs.extend([deco.slot_level] * shortfall)
            if not feasible:
                continue
            needs.sort(reverse=True)
            free = state.slots
            for size in needs:
                free = consume_slot(free, size)
                if free is None:
                    break
            else:
                return True
        return False

    def _rank_state(self, state: SearchState, pieces_chosen: int) -> float:
        scoring = self.scoring
        total = 0.0
        for idx, name in enumerate(self.context.relevant_skills):
            total += scoring.score(name, state.levels[idx])

        for idx, name in enumerate(self.context.relevant_bonuses):
            count = state.bonus_counts[idx] + self.extra_bonus_pieces.get(name, 0)
            if count == 0:
                continue
            info = self.context.bonus_registry[name]
            level = info.level_for(count)
            total += scoring.score(name, level)
            # partial credit for being part-way to the next threshold
            next_thresholds = [t for t in info.thresholds if t > count]
            if next_thresholds and pieces_chosen < len(PIECE_TYPES):
                need = min(next_thresholds)
                step = scoring.marginal(name, level)
                total += step * (count / need) * BONUS_PROGRESS_CREDIT

        total += scoring.defense_points * state.defense_value_sum / len(PIECE_TYPES)

        n1, n2, n3 = state.slots
        p1, p2, p3 = self._slot_potential
        total += n1 * p1 + n2 * p2 + n3 * p3
        return total

    def _search_armour(self) -> list[SearchState]:
        order = sorted(PIECE_TYPES, key=lambda t: len(self.context.candidates[t]))
        empty_levels = (0,) * len(self.context.relevant_skills)
        empty_bonuses = (0,) * len(self.context.relevant_bonuses)
        beam = [
            SearchState(
                profiles=(),
                levels=empty_levels,
                slots=(0, 0, 0),
                defense_value_sum=0.0,
                bonus_counts=empty_bonuses,
                rank=0.0,
            )
        ]

        for stage, piece_type in enumerate(order, start=1):
            candidates = self.context.candidates[piece_type]
            expanded: list[SearchState] = []
            for state in beam:
                for profile in candidates:
                    levels = tuple(
                        a + b for a, b in zip(state.levels, profile.skill_levels)
                    )
                    slots = (
                        state.slots[0] + profile.slots[0],
                        state.slots[1] + profile.slots[1],
                        state.slots[2] + profile.slots[2],
                    )
                    counts = list(state.bonus_counts)
                    for idx in profile.bonus_indices:
                        counts[idx] += 1
                    new_state = SearchState(
                        profiles=state.profiles + (profile,),
                        levels=levels,
                        slots=slots,
                        defense_value_sum=state.defense_value_sum
                        + profile.defense_value,
                        bonus_counts=tuple(counts),
                        rank=0.0,
                    )
                    new_state.rank = self._rank_state(new_state, stage)
                    expanded.append(new_state)

            expanded.sort(key=lambda s: s.rank, reverse=True)
            if stage < len(order):
                beam = self._truncate(expanded, self.beam_width)
            else:
                # Sets that can still satisfy the mandatory skills come first,
                # otherwise the pool fills with high-scoring but invalid sets.
                expanded.sort(
                    key=lambda s: (not self._mandatory_reachable(s), -s.rank)
                )
                beam = self._truncate(expanded, self.final_pool)

        return beam

    def _truncate(self, states: list[SearchState], width: int) -> list[SearchState]:
        """Keep the best states, but cap how many share a bonus signature.

        Without this the beam converges on a single build neighbourhood and the
        'distinct bonuses' results have nothing to draw from.
        """
        if not self.context.relevant_bonuses or len(states) <= width:
            return states[:width]

        quota = max(1, width // max(2, 2 ** len(self.context.relevant_bonuses)))
        kept: list[SearchState] = []
        overflow: list[SearchState] = []
        seen: dict[tuple[int, ...], int] = {}
        for state in states:
            signature = state.bonus_counts
            count = seen.get(signature, 0)
            if count < quota and len(kept) < width:
                kept.append(state)
                seen[signature] = count + 1
            else:
                overflow.append(state)

        if len(kept) < width:
            kept.extend(overflow[: width - len(kept)])
        return kept

    def _evaluate(self, state: SearchState) -> GearSet | None:
        # The search visits slots in candidate-count order; present them head->legs.
        pieces = sorted(
            (p.piece for p in state.profiles),
            key=lambda piece: PIECE_TYPES.index(piece.piece_type),
        )

        base_levels: dict[str, int] = {}
        for piece in pieces:
            for granted in piece.skills:
                base_levels[granted.name] = base_levels.get(granted.name, 0) + granted.level

        best: GearSet | None = None
        for talisman in self._shortlist_talismans(base_levels):
            candidate = self._build_set(pieces, talisman, base_levels, state)
            if candidate is None:
                continue
            if best is None or (
                candidate.constraint_level,
                -candidate.total_score,
            ) < (best.constraint_level, -best.total_score):
                best = candidate
        return best

    def _shortlist_talismans(self, base_levels: dict[str, int]) -> list[Talisman]:
        scoring = self.scoring
        scored: list[tuple[float, int, Talisman]] = []
        for index, talisman in enumerate(self.game.talismans):
            gain = 0.0
            for granted in talisman.skills:
                current = base_levels.get(granted.name, 0)
                gain += scoring.score(granted.name, current + granted.level) - scoring.score(
                    granted.name, current
                )
            sizes, _ = talisman_slot_sizes(talisman)
            for size in sizes:
                gain += self._slot_potential[min(size, 3) - 1]
            scored.append((gain, index, talisman))

        scored.sort(key=lambda item: (-item[0], item[1]))
        return [t for _, _, t in scored[:TALISMAN_SHORTLIST]]

    def _build_set(
        self,
        pieces: list[ArmorPiece],
        talisman: Talisman,
        base_levels: dict[str, int],
        state: SearchState,
    ) -> GearSet | None:
        context = self.context
        scoring = self.scoring

        levels = dict(base_levels)
        for granted in talisman.skills:
            levels[granted.name] = levels.get(granted.name, 0) + granted.level

        talisman_sizes, weapon_slots = talisman_slot_sizes(talisman)

        # Every physical decoration slot's size. Reservation is decided on
        # this concrete list (smallest sizes first) rather than an abstract
        # slot-count, so the slots actually left empty are guaranteed to be
        # the least valuable ones - see _assign_decorations.
        all_sizes = sorted(
            size for piece in pieces for size in piece.slots if size
        )
        all_sizes.extend(talisman_sizes)
        all_sizes.sort()

        options = decoration_options(levels, context)

        chosen_counts: tuple[int, ...] = ()
        reserved = 0
        final_levels: dict[str, int] = {}
        best_level = 3
        max_reserve = min(self.reserved_slots, len(all_sizes))
        for reserve in range(max_reserve, -1, -1):
            available_counts = slot_counts(all_sizes[reserve:])
            _value, counts = fill_slots(options, available_counts)
            trial_levels = dict(levels)
            for option, count in zip(options, counts):
                if count:
                    trial_levels[option.skill] = trial_levels.get(option.skill, 0) + count
            level_met = constraint_level_met(trial_levels, scoring)
            if level_met < best_level:
                best_level = level_met
                chosen_counts, reserved, final_levels = counts, reserve, trial_levels
            if level_met == 0:
                break

        if not final_levels:
            return None

        placements, free_slots = self._assign_decorations(
            pieces, talisman, talisman_sizes, options, chosen_counts, reserved
        )

        active_bonuses = self._active_bonuses(pieces)
        for bonus in active_bonuses:
            final_levels[bonus.name] = bonus.level

        # Pieces alone can push a skill past its cap; report the effective level.
        for name, level in final_levels.items():
            skill = scoring.by_name.get(name)
            if skill is not None and level > skill.max_level:
                final_levels[name] = skill.max_level

        skill_score = sum(
            scoring.score(name, level) for name, level in final_levels.items()
        )
        defense_total = sum(p.defense.max for p in pieces)
        defense_component = (
            scoring.defense_points * state.defense_value_sum / len(PIECE_TYPES)
        )

        return GearSet(
            pieces=pieces,
            talisman=talisman,
            skill_levels=final_levels,
            active_bonuses=active_bonuses,
            placements=placements,
            free_slots=free_slots,
            reserved_slots=reserved,
            weapon_slots=weapon_slots,
            defense_total=defense_total,
            skill_score=skill_score,
            defense_score=defense_component,
            total_score=skill_score + defense_component,
            constraint_level=constraint_level_met(final_levels, scoring),
            pinned_types=frozenset(context.pinned),
        )

    def _assign_decorations(
        self,
        pieces: list[ArmorPiece],
        talisman: Talisman,
        talisman_sizes: list[int],
        options: list[DecoOption],
        counts: tuple[int, ...],
        reserve_count: int,
    ) -> tuple[list[SlotAssignment], list[int]]:
        # Built in each piece's own slot order (not size order), so the GUI's
        # per-piece display matches the source data's slot ordering.
        slots: list[SlotAssignment] = []
        for piece in pieces:
            for size in piece.slots:
                if size:
                    slots.append(SlotAssignment(source=piece.name, size=size))
        for size in talisman_sizes:
            slots.append(SlotAssignment(source=talisman.name, size=size))

        # The `reserve_count` smallest slots are held back entirely - never
        # offered to the fitter below - matching the sizes fill_slots was
        # given in _build_set, so a size-1 slot is always sacrificed before a
        # size-2/3 one.
        reserved_ids = {
            id(s) for s in sorted(slots, key=lambda s: s.size)[:reserve_count]
        }
        eligible = [s for s in slots if id(s) not in reserved_ids]

        wanted: list[Decoration] = []
        for option, count in zip(options, counts):
            wanted.extend([option.decoration] * count)
        wanted.sort(key=lambda d: -d.slot_level)

        for deco in wanted:
            fit = min(
                (s for s in eligible if s.decoration is None and s.size >= deco.slot_level),
                key=lambda s: s.size,
                default=None,
            )
            if fit is None:
                continue
            fit.decoration = deco

        free = sorted(s.size for s in slots if s.decoration is None)
        return slots, free

    def _active_bonuses(self, pieces: list[ArmorPiece]) -> list[ActiveBonus]:
        counts: dict[str, int] = {}
        for piece in pieces:
            for bonus in piece.set_bonuses:
                base = bonus_base_name(bonus.name)
                counts[base] = counts.get(base, 0) + 1

        # A weapon-granted bonus point can activate a bonus no chosen armour
        # piece carries at all, so consider every name with an extra point too.
        names = set(counts) | {n for n, e in self.extra_bonus_pieces.items() if e}

        active: list[ActiveBonus] = []
        for name in names:
            info = self.context.bonus_registry.get(name)
            if info is None:
                continue  # extra point on a bonus with no known armour source
            armour_pieces = counts.get(name, 0)
            extra = self.extra_bonus_pieces.get(name, 0)
            total = armour_pieces + extra
            level = info.level_for(total)
            if level <= 0:
                continue
            active.append(
                ActiveBonus(
                    name=name,
                    bonus_type=info.bonus_type,
                    pieces=armour_pieces,
                    extra=extra,
                    level=level,
                    effects=[
                        effect
                        for effect, threshold in zip(info.effects, info.thresholds)
                        if total >= threshold
                    ],
                )
            )
        active.sort(key=lambda b: (b.bonus_type, -b.level, b.name))
        return active

    def run(self, tiers=DEFAULT_TIERS) -> tuple[list[GearSet], int]:
        states = self._search_armour()

        evaluated: list[GearSet] = []
        for state in states:
            gear_set = self._evaluate(state)
            if gear_set is not None:
                evaluated.append(gear_set)

        evaluated.sort(key=lambda s: (s.constraint_level, -s.total_score))
        if not evaluated:
            return [], 2

        wanted = sum(tier.count for tier in tiers)
        constraint_level = 2
        for level in (0, 1, 2):
            if sum(1 for s in evaluated if s.constraint_level <= level) >= wanted:
                constraint_level = level
                break

        pool = [s for s in evaluated if s.constraint_level <= constraint_level]
        return select_diverse(pool, tiers), constraint_level


def select_diverse(pool: list[GearSet], tiers) -> list[GearSet]:
    """Pick sets band by band so the results are not near-identical.

    Each band relaxes its own rules rather than returning short, so the caller
    always gets the requested number of sets when the pool can supply them.
    """
    chosen: list[GearSet] = []
    used_signatures: set[frozenset[tuple[str, int]]] = set()
    taken: set[tuple[str, ...]] = set()

    def piece_diff(gear_set: GearSet) -> int:
        if not chosen:
            return len(PIECE_TYPES)
        return min(
            sum(1 for a, b in zip(gear_set.piece_names, other.piece_names) if a != b)
            for other in chosen
        )

    for tier in tiers:
        added = 0
        rules = [(tier.require_new_bonuses, tier.min_piece_diff)]
        if tier.require_new_bonuses:
            rules.append((False, max(tier.min_piece_diff, 2)))
        if tier.min_piece_diff > 1:
            rules.append((False, 1))

        for rule_index, (require_new_bonuses, min_piece_diff) in enumerate(rules):
            if added >= tier.count:
                break
            label = tier.label if rule_index == 0 else f"{tier.label} (relaxed)"
            for gear_set in pool:
                if added >= tier.count:
                    break
                names = gear_set.piece_names
                if names in taken:
                    continue
                if require_new_bonuses and gear_set.bonus_signature in used_signatures:
                    continue
                if piece_diff(gear_set) < min_piece_diff:
                    continue
                gear_set.tier = label
                chosen.append(gear_set)
                taken.add(names)
                used_signatures.add(gear_set.bonus_signature)
                added += 1

    return chosen


def optimise(
    game: GameData,
    scoring: Scoring,
    beam_width: int = BEAM_WIDTH,
    final_pool: int = FINAL_POOL,
    reserved_slots: int = RESERVED_SLOTS,
    tiers=DEFAULT_TIERS,
    extra_bonus_pieces: dict[str, int] | None = None,
    pinned_pieces: dict[str, str] | None = None,
) -> tuple[list[GearSet], int, Optimiser]:
    optimiser = Optimiser(
        game,
        scoring,
        beam_width=beam_width,
        final_pool=final_pool,
        reserved_slots=reserved_slots,
        extra_bonus_pieces=extra_bonus_pieces,
        pinned_pieces=pinned_pieces,
    )
    sets, constraint_level = optimiser.run(tiers)
    return sets, constraint_level, optimiser


def build_tiers(count: int) -> tuple[DiversityTier, ...]:
    """Scale the default 3/3/4 banding to the requested number of sets."""
    if count >= 10:
        return DEFAULT_TIERS
    shares = [0, 0, 0]
    for i in range(count):
        shares[i % 3] += 1
    return tuple(
        DiversityTier(t.label, share, t.min_piece_diff, t.require_new_bonuses)
        for t, share in zip(DEFAULT_TIERS, shares)
        if share
    )


def gear_set_filename(db_path: Path) -> str:
    """Results filename that can't be mistaken for a skills DB."""
    stem = db_path.stem
    for prefix in ("skills_DB_", "skills_db_", "skills_"):
        if stem.startswith(prefix):
            stem = stem[len(prefix) :]
            break
    return f"{stem}_gear_sets.yaml"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skills-db",
        default="skills_outputs/skills_DB_burst.yaml",
        help="weighted skills YAML to optimise against",
    )
    parser.add_argument("--count", type=int, default=10, help="number of sets to return")
    parser.add_argument("--beam", type=int, default=BEAM_WIDTH, help="beam width")
    parser.add_argument(
        "--reserve",
        type=int,
        default=RESERVED_SLOTS,
        help="slots to hold back for resistance jewels",
    )
    parser.add_argument("--output", help="YAML file to write the sets to")
    for piece_type in PIECE_TYPES:
        parser.add_argument(
            f"--pin-{piece_type}",
            metavar="PIECE",
            help=f"force the {piece_type} slot to this armour piece, by name",
        )
    args = parser.parse_args()

    from optimiser_report import render_console, write_yaml

    db_path = Path(args.skills_db)
    game = load_game_data()
    scoring = Scoring(load_skills(db_path))

    pinned_pieces = {
        piece_type: getattr(args, f"pin_{piece_type}")
        for piece_type in PIECE_TYPES
        if getattr(args, f"pin_{piece_type}")
    }
    try:
        pinned = resolve_pins(game, pinned_pieces)
    except ValueError as exc:
        parser.error(str(exc))

    unreachable = Context(game, scoring).unreachable_weighted_skills()
    if unreachable:
        print(
            "Warning: these weighted skills cannot come from armour, talismans or "
            "armour decorations and were ignored: " + ", ".join(unreachable)
        )

    sets, constraint_level, optimiser = optimise(
        game,
        scoring,
        beam_width=args.beam,
        reserved_slots=args.reserve,
        tiers=build_tiers(args.count),
        pinned_pieces=pinned_pieces,
    )

    print(render_console(sets, scoring, constraint_level, db_path, pinned=pinned))

    output = Path(args.output) if args.output else Path("optimiser_outputs") / (
        gear_set_filename(db_path)
    )
    write_yaml(sets, scoring, constraint_level, db_path, output)
    print(f"\nWrote {len(sets)} sets to {output}")


if __name__ == "__main__":
    main()
