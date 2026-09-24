# Monster Hunter Wilds Build Optimiser

Build your own gear loadouts for *Monster Hunter Wilds* by describing what you want instead of picking pieces by hand. You assign a **weight** to each skill ("how much do I care about this?") and a **level weight** ("how important is the level vs just having it at all?"), and the optimiser searches through every High Rank armor piece, craftable talisman and decoration for combinations that score best against your weighting.

It deliberately does not return ten near-identical builds: results are banded into *closest variants*, *distinct builds* (different pieces) and *distinct bonuses* (new set/group bonus effects).

## Requirements

- Python 3 with `tkinter` — tested on Python 3.13; the GUI needs the tkinter package, which standard CPython installers include
- PyYAML: `pip install pyyaml`. The search engine itself needs nothing beyond PyYAML

The GUI (`launch_gui.bat`) launches it with `pythonw`, so no console window appears — but that also means a missing dependency fails silently and nothing opens. If double-clicking does nothing, run `python skills_gui.py` from a terminal to see the traceback.

## Quick start (GUI)

1. Double-click `launch_gui.bat`.
2. On the **Skill Weights** tab, select skills and give each a weight / level weight (see [How scoring works](#how-scoring-works)). Filter with the *Type* dropdown to work through armor/weapon/set bonus/group/food skills in order.
3. **Run Optimiser** works straight off your edited values, saved or not — saving is only for keeping the weighting around afterwards. When you do save, the default filename is `skills_weighted.yaml`, written next to whichever file you opened (usually the repo root, so it will show as an untracked git file unless you use *Browse…* to put it in `skills_outputs/`). Keep separate weighted copies side by side for different build goals instead of overwriting each time. Save refuses `skills_default.yaml` outright: the Save box writes beside the loaded file, which is usually that one, so typing its name would otherwise replace the master data. Save also asks before replacing any other existing file, and closing the window or opening another skills file with edits unsaved asks before discarding them.
4. Optionally:
   - pick a **Gogma weapon** set bonus / group skill in the "Gogma weapon skills" box if your weapon contributes pieces toward one of them — see [How it works](#how-it-works) for what that credit does;
   - load custom talismans built on the other tab, so they join the optimiser's charm pool without touching `craftable_talismans.yaml`;
   - adjust *Reserved slots* (defaults to 2) for resistance jewels you plan to slot yourself. The smallest slots in the set are the ones held back, so they're size 1 unless the set runs out of those.
5. Results open in a second window with Previous/Next navigation.

### Custom Talismans tab

Build charms the game doesn't have: up to three skills plus up to three armour and three weapon decoration slots per talisman, saved under `custom_talismans_outputs/`. Only armor-type skills are offered — the optimiser builds armor sets only. Load them on the main tab when you want them considered (e.g. a charm equivalent of your best appraised one).

*Select/Create File…* either opens an existing file for editing or names a new one, and writes nothing by itself, so it doesn't ask to replace an existing file. It opens on its own the first time you visit the tab and after that only when clicked. A talisman can't list the same skill twice or take a level below 1: two rows of one skill would stack past the max-level check a single row gets. **Delete Selected rewrites the file immediately**, so it asks first; there is no undo.

## Command line

```
python optimiser.py --skills-db skills_weighted.yaml --count 10
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--skills-db` | `skills_outputs/skills_DB_burst.yaml` | Weighted skills YAML to optimise against |
| `--count` | `10` | Number of sets returned (distributed round-robin across the diversity bands); at least 1 |
| `--beam` | `5000` | Beam-search width; see [How it works](#how-it-works) for why that number; at least 1 |
| `--reserve` | `2` | Slots held back for resistance jewels, smallest first; 0 or more |
| `--output` | in `optimiser_outputs/`, named from the input with any `skills_` prefix stripped | Where sets are written, so a results file can never be mistaken for a skills DB (`load_data.py` refuses to load one as weights anyway) |
| `--pin-head` … `--pin-legs` | none | Force that slot to a named armour piece; see [Pinning armour](#pinning-armour) |
| `--relax` | off | Allow sets that miss a mandatory skill rather than returning fewer |

**Always pass `--skills-db`.** The default points at a weighted file that isn't committed (everything under the output folders is gitignored), so a bare `python optimiser.py` dies with `FileNotFoundError`. Example: input `skills_weighted.yaml` writes results to `optimiser_outputs/weighted_gear_sets.yaml`. A `--count` or `--beam` below 1, or a negative `--reserve`, is rejected up front: each used to return an empty result with no reason given.

The console render includes a header naming your mandatory skills and any pinned pieces. With `--relax` it also names the constraint tier it ended on and adds a "constraints were relaxed" note (see [How scoring works](#how-scoring-works)); without it there is nothing to relax, so a run either meets your requirements or says why it couldn't. Weighted skills that armour, talismans and armour jewels simply cannot supply (mostly weapon and food skills) are ignored with a warning naming them; the GUI doesn't show this warning. A *required* one is not ignored: without `--relax` it empties the result, and the no-sets message names it as the reason, in the GUI too.

## How scoring works

Both values sit on roughly a 0–5 scale; anything at or above 5 has special meaning:

- **weight ≥ 5** → the skill is *mandatory*: a build missing it is not returned at all.
- **level_weight ≥ 5 (with weight ≥ 5)** → mandatory-max: the build must reach the skill's max level. With relaxing turned on (below), this is the first requirement dropped.

Below those thresholds, weights are continuous. A high-weight skill earns value for every point of progress toward its max level; `level_weight` decides how much of the score lives in *having levels* vs merely *owning the skill*. At 0, even a lone level-1 counts for full credit; at 5, the whole value sits in climbing to max. Negative values actively penalise skills you don't want (e.g. resistances eating your slots); zero ignores them entirely.

The GUI offers −1 to 5 as a dropdown: −1 avoids, 0 ignores, 1–4 scale how much you want the skill, 5 makes it mandatory. The file itself still accepts any number, so a hand-edited value outside that range is kept and shown rather than snapped onto the list. −1 sounds too mild to do anything — one point of it scores −1 against a weight-4 skill's 16 — but a skill you are avoiding is normally incidental to whatever piece carries it, so any penalty at all tips the choice. Two skills that appear in all ten default sets vanish completely at −1. Where it will *not* help is a skill riding on a piece you want for other reasons; nothing in this range outweighs a weight-4 skill sitting on the same armour.

Defense is also worth something: each piece's maximum defense is normalized between 62 and 94 and raised to the power 2.5 (so low-defense pieces are penalized well beyond their linear share), with whole-set defense worth about one weight-2 skill.

The exponents matter: skill value scales as `weight²`, deliberately, because at exponent 1 a weight-2 skill sitting at level 1 outscored a further level of a weight-4 skill and sets filled up with shallow filler; above ~2.5 the gap becomes extreme enough that mid-weight skills lose their depth again. The constants (`WEIGHT_EXPONENT`, `LEVEL_WEIGHT_EXPONENT`, `DEFENSE_EXPONENT`) sit at the top of [optimiser.py](optimiser.py) with the reasoning for each value written beside it if you want to push quality or speed in a different direction.

Mandatory is a **hard filter**. A set that misses a mandatory skill is never returned, even when that means fewer sets than you asked for, or none. Relaxing is opt-in — `--relax`, or the **Allow sets missing a required skill** checkbox in the GUI — and only then does the old ranking-tier behaviour apply, dropping max-levels first and then the requirement entirely. The filter is deliberate: relaxing by default meant the GUI, which never showed the tier, presented a set list that had quietly abandoned a requirement as though it had met one.

When nothing qualifies you get the reason at one of two strengths. Required set bonuses and group skills are checked arithmetically **before the search runs**: no piece carries more than one set bonus, and none carries more than one group skill, so the pieces several of them need add up rather than overlapping, and a total above the slots left free after pinning is unreachable outright. A set bonus and a group skill *can* share a piece, so mixing the two kinds is checked exactly instead: every way of filling the free slots is tried against just the required bonuses, which is how Gore Magala's Tyranny at max plus Alluring Pelt (4 + 3 pieces, no piece carrying both) is ruled out before searching. Required skills that nothing in the data grants, such as weapon skills, are caught at the same stage. Anything that survives that check and still finds nothing reports how far short each requirement fell, worded as what the search didn't find rather than what doesn't exist — the beam is a heuristic and hasn't earned the stronger claim.

### Set bonuses as requirements

Set bonuses and group skills are ordinary rows in the skills file, so they take weights like anything else and need no separate mechanism. `weight: 5` requires the bonus at level 1; `weight: 5` with `level_weight: 5` requires it at max. For Gore Magala's Tyranny that's the 2-piece and 4-piece effects. Every set bonus in the data has exactly two levels and every group skill has one, so those two settings already cover every requirement the game can express.

### Pinning armour

Any of the five slots can be fixed to a specific piece, leaving the rest to the search — for asking what's possible around a set you've already decided on. On the CLI that's `--pin-waist "Gore Coil α"`; in the GUI it's the **Fixed gear** panel on the Skill Weights tab, where each slot lists the armour sets that have a piece for it. A set plus a slot identifies a piece uniquely across the whole armour data, so picking the set is enough and there's no second dropdown. The Gogma weapon selectors live in that same panel, being in effect a sixth piece's worth of set bonus.

A pinned slot skips dominated-piece pruning, since pruning only chooses between alternatives and a pinned slot has none. Its single candidate also sorts it first in the search's stage order, so every later choice is ranked with the pinned piece already counted. Pinned pieces are marked `*` in the results.

Pinning narrows the diversity bands: *distinct builds* wants a 2-piece difference, which four pins make impossible, so it relaxes and tags its results `(relaxed)`. That's arithmetic, not a warning worth acting on.

## How it works

A beam search over the five equipment slots: one piece per slot plus exactly one talisman (each complete armour combination is tried against the five best talismans), then decorations placed by an *exact* search over the free slots using the cheapest armor jewel for each skill — and only where a gem raises a weighted skill, never to fill space. The smallest `--reserve` slots stay open for per-hunt resistance jewels; they're released automatically only if holding them back would miss a mandatory skill. Required set bonuses count toward that decision too; leave them out and a required bonus reads as missing on every attempt, so the reserved slots are never released even when a required skill needs them.

Several choices keep the beam from collapsing or wasting effort:

- Pieces that another piece beats or ties on every relevant skill, slot count and defense are pruned first — but only within the same set-bonus group, since a piece carrying a weighted bonus is never interchangeable with one that lacks it.
- The beam caps how many partial states share a given set/group-bonus signature. Without the cap it converges on one build neighborhood and the *distinct bonuses* band has nothing to draw from.
- The width of 5000 (and 2000 final combinations) is where quality plateaus: 3000/1200 left about 1% of score on the table, while 9000/15000 found nothing better at several times the runtime.

The Gogma selectors credit **one extra piece** toward a chosen set bonus and one toward a chosen group skill — standing in for the bonus point your weapon carries — which can complete a bonus that no chosen armor pieces carry at all. They only offer bonuses that some armour piece carries. A bonus's piece thresholds come from the armour data, so one that no armour carries (Soul of the Dark Knight) has none, and a point credited to it would do nothing.

### Result bands

| Band | Count (at `--count 10`) | Rule |
| --- | --- | --- |
| closest variants | 3 | Differs from every chosen set by at least 1 piece |
| distinct builds | 3 | Differs from every chosen set by at least 2 pieces |
| distinct bonuses | 4 | Set/group bonus combination not already in the results |

A band that can't fill itself relaxes its own rule and tags those sets `(relaxed)`, so you still get the count requested. `--count` below 10 spreads the sets round-robin across the three bands instead of shorting one of them; above 10 the 3/3/4 split is kept and the extra sets are dealt round-robin on top.

## Data scope

The optimiser sees a deliberately narrow slice of the game:

| Included | Excluded | Why |
| --- | --- | --- |
| High Rank armor only | Low Rank armor | Not the tool's target |
| Craftable talismans | Appraised talismans | Random skills and slots can't be enumerated — build equivalents in the Custom Talismans tab instead |
| Armor jewels (loaded) | Weapon jewels | Loaded into memory but never placed; builds assume you equip your own weapons |

All game data was compiled by hand from the community wiki at [game8.co](https://game8.co/games/Monster-Hunter-Wilds). Every record carries a `source_url` back to its page, and file headers list the source archives. Set bonus skills use their 2-piece/4-piece tiers as levels; group and food skills are on/off (no levels). The one exception is each skill's `levels` list — what every level actually does, in the game's own text (Fire Resistance 3 is "Fire resistance +20 Defense +10"), shown under the description when you click a skill. Game8 has no machine-readable export and keeps that text on one page per skill, so it was pulled in a single request from the [Wilds API](https://wilds.mhdb.io/en/skills), which carries the in-game strings; spot checks against game8's skill pages agree on every number. Food skills have no per-level text in either source. The skill file also records each skill's *scaling* class (linear/geometric/etc.), inferred from how the numeric effect grows across levels — useful context when choosing weights, though it doesn't feed the optimiser directly.

Need current record counts? Run `python load_data.py` rather than trusting any number written down; the data files get updated more often than this document does.

## Repository layout

| File | Purpose |
| --- | --- |
| `skills_gui.py` | Tkinter GUI: skill weighting, custom talismans, optimiser runner with results window |
| `optimiser.py` | Beam-search engine and scoring model (`Scoring`, `Optimiser`, `GearSet`) plus the CLI entry point. Deliberately print-free so the GUI reuses it directly |
| `optimiser_report.py` | Rendering only: console text, inline result view for the GUI window, and YAML export of results |
| `load_data.py` | Typed dataclasses (`Skill`, `ArmorPiece`, `Talisman`, `Decoration`) and loaders with validation (e.g. refuses to treat a results file as a skills DB). Run it directly for record counts |
| `skills_default.yaml` | Every skill: armor, weapon, set bonus, group and food — descriptions, max level, per-level effects, scaling class, per-source URLs. `weight`/`level_weight` start at 0 placeholders |
| `high_rank_armor.yaml` | All High Rank pieces with defense, resistances, skills, transcended slot values where applicable (`slots_source` says which are listed) and the set/group bonuses each piece participates in |
| `craftable_talismans.yaml` | Smithy-crafted charms only. Craftables carry no decoration slots, but the schema keeps a placeholder for custom ones built in the GUI |
| `decorations.yaml` | Armor and weapon jewels with their slot sizes and skills |
| `launch_gui.bat` | Windows launcher: runs `pythonw skills_gui.py` from the repo folder |
| `gui_state.json` | Written by the GUI when dark mode is toggled and on close; holds only that setting. Gitignored |
| `skills_outputs/`, `optimiser_outputs/`, `custom_talismans_outputs/` | Your generated files. Contents are gitignored, but each folder keeps a tracked `.keepempty` marker so they exist on clone |

## License

[PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0) — see [license.txt](license.txt). Free to use and share for anything non-commercial; commercial use requires a separate agreement with the licensor.
