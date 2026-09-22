# MHWilds-build-optimiser

Gear set optimiser for Monster Hunter Wilds high rank. You give each skill a weight; it searches for complete builds (five armour pieces, one talisman, decorations) that maximise the weighted total, and returns ten deliberately different sets instead of ten near-copies of the best one.

## Requirements

Python (developed on 3.13), PyYAML (`pip install pyyaml`), and Tkinter for the GUI, which the python.org Windows installer includes. The search engine itself needs nothing beyond PyYAML.

## Running it

**GUI.** Double-click `launch_gui.bat`. It starts `skills_gui.py` with `pythonw`, which has no console, so a missing dependency fails silently and nothing opens. Run `python skills_gui.py` from a terminal to see the traceback.

The **Skill Weights** tab loads a skills file, filters it by type, and edits `weight` and `level_weight` per skill. **Run Optimiser** works from the edited values whether or not they have been saved, and opens a results window that steps through the sets one at a time. **Save** writes the edited skills file. The **Custom Talismans** tab builds talismans and saves them to their own file (see [Data scope](#data-scope)).

**CLI.**

```
python optimiser.py --skills-db skills_weighted.yaml --count 10
```

| Flag | Default | Effect |
|---|---|---|
| `--skills-db` | `skills_outputs/skills_DB_burst.yaml` | Weighted skills file to optimise against |
| `--count` | `10` | Number of sets to return |
| `--beam` | `5000` | Beam width; see [How the search works](#how-the-search-works) |
| `--reserve` | `2` | Decoration slots held back for resistance jewels |
| `--output` | `optimiser_outputs/<name>_gear_sets.yaml` | Where to write the results |

**Always pass `--skills-db`.** The default points at a file that is not in the repo, so a bare `python optimiser.py` dies with `FileNotFoundError`. Results are written with the `skills_` prefix stripped from the input name (`skills_weighted.yaml` becomes `weighted_gear_sets.yaml`) so a results file is never named like a skills file.

## Weights

Every skill in `skills_default.yaml` ships with `weight: 0` and `level_weight: 0`, and a weight of 0 means the skill is ignored. Copy the file, or edit in the GUI and save under a new name, rather than changing `skills_default.yaml`: it is what `load_data.py` loads for the skill list when the optimiser runs from the CLI.

| Field | Meaning |
|---|---|
| `weight` | How much the skill matters. Negative values actively avoid it. **5 or above makes the skill mandatory.** |
| `level_weight` | How much of that value sits in the levels rather than in having the skill at all. At 0, level 1 earns the full value; at 5, value climbs in proportion to progress toward max level. |

A skill needs both at 5 to be mandatory-max, meaning it must reach its maximum level.

**Mandatory is a ranking tier, not a hard filter.** If fewer sets meet the requirement than were asked for, the optimiser relaxes it (all mandatory skills at max, then all mandatory skills present, then nothing) rather than returning short. The CLI prints the tier it ended on and a `NOTE: constraints were relaxed` line. The GUI discards the tier, so relaxation is silent there; the `[mandatory]` tags on each skill line in the results window are the only sign a requirement was missed.

### Scoring

A skill's value is `weight ** 2`, sign preserved, scaled by how far up its levels the set gets. Squaring is deliberate: at an exponent of 1, a weight-2 skill at level 1 outscored a further level of a weight-4 skill, and sets filled up with shallow level-1 filler. Above about 2.5 the gap becomes extreme enough that mid-weight skills start losing their depth again. The constants are `WEIGHT_EXPONENT`, `LEVEL_WEIGHT_EXPONENT` and `DEFENSE_EXPONENT` at the top of `optimiser.py`, each with the reasoning for its value beside it.

Whole-set defence is scored as worth about one weight-2 skill. Each piece's max defence is normalised between 62 and 94 and raised to the 2.5th power, so a low-defence piece is penalised well beyond its linear share.

## How the search works

A beam search over the five equipment slots, keeping 5000 partial sets per stage and 2000 complete armour combinations for full evaluation. It is a heuristic, not an exhaustive search. The width is 5000 because the result plateaus there: 3000/1200 left about 1% of score on the table, and 9000/15000 found nothing better at several times the runtime.

Several choices exist to stop the beam collapsing or wasting effort:

- Pieces that another piece beats or ties on every relevant skill, slot and defence value are pruned first, but only within the same weighted set-bonus group, since a piece carrying a weighted bonus is never interchangeable with one that lacks it.
- The beam caps how many states share a set-bonus signature. Without the cap it converges on one build neighbourhood and the distinct-bonus results have nothing to draw from.
- Each complete armour combination is tried with the five best talismans for that combination, and decorations are then placed by an exact search over the free slots, using the cheapest armour gem for each skill and only where it raises a weighted skill.
- The smallest `--reserve` slots are left empty for resistance jewels, and released only if holding them back would miss a mandatory skill.

### Result bands

| Band | Count | Rule |
|---|---|---|
| closest variants | 3 | Differs from every chosen set by at least 1 piece |
| distinct builds | 3 | Differs from every chosen set by at least 2 pieces |
| distinct bonuses | 4 | Set/group bonus combination not already in the results |

A band that cannot fill itself relaxes its own rule and labels the sets `(relaxed)`, so the requested count is met whenever the pool can supply it. `--count` below 10 spreads the sets round-robin across the three bands.

The GUI's **Gogma weapon skills** selectors credit one extra piece toward a chosen set bonus and one toward a chosen group skill, standing in for the bonus point a Gogma weapon carries.

## Data scope

The optimiser sees a deliberately narrow slice of the game.

| Included | Excluded | Why |
|---|---|---|
| High rank armour | Low rank armour | Not the target of the tool |
| Craftable talismans | Appraised talismans | Their skills and slots are random, so they can't be enumerated; build them in the Custom Talismans tab instead |
| Armour decorations | Weapon decorations | Loaded, but only armour decorations are placed |

Custom talismans are loaded through **Load Custom Talismans...** on the main tab and added to the pool for that run only; `craftable_talismans.yaml` is never modified. The builder allows up to 3 skills, 3 armour slots and 3 weapon slots per talisman, and only offers armour-type skills.

A weighted skill that armour, talismans and armour decorations cannot supply (weapon and food skills, mostly) is ignored. The CLI prints a warning naming them; the GUI does not.

All data was scraped from game8.co. The URLs are in each YAML file's header comment, and every record carries its own `source_url`. Run `python load_data.py` for the current record counts rather than trusting a number here; the data files are updated more often than this file is.

## Files

| Path | Purpose |
|---|---|
| `optimiser.py` | Scoring, search and the CLI. Free of printing, so the GUI imports `optimise()` directly |
| `optimiser_report.py` | Console rendering, the GUI's inline renderer, and the results YAML writer |
| `skills_gui.py` | Tkinter GUI: weights, custom talismans, optimiser runner |
| `load_data.py` | Loads the four data files into dataclasses |
| `skills_default.yaml` | All skills, every weight at 0 |
| `high_rank_armor.yaml` | Armour pieces |
| `craftable_talismans.yaml` | Craftable talismans |
| `decorations.yaml` | Decorations |
| `skills_outputs/`, `optimiser_outputs/`, `custom_talismans_outputs/` | Gitignored output folders, kept in the repo by `.keepempty` |

**The GUI's Save writes beside the file you loaded**, which by default is the repo root, not `skills_outputs/`. The root is not gitignored, so a saved weights file shows up as untracked. Use **Browse...** to save into `skills_outputs/`.

## Licence

PolyForm Noncommercial 1.0.0. See `license.txt`.
