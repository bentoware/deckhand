# deckhand organize

Help the user get from messy decks, tags, duplicates, or queues to a collection that supports the way they actually study. Prefer the smallest useful improvement over a grand taxonomy.

## Intent-first recipe

- Infer the operational goal behind the complaint: fewer decks, cleaner tags, finding material, separating review queues, removing duplicates, or creating a temporary study view.
- Inspect live structure before proposing changes: deck tree, tag list, counts, empty decks, near-duplicate tags (`pharm` vs `pharmacology`), and duplicate notes (same first field — `col.find_notes` with `dupe:` or field comparison).
- Prefer reversible, scheduling-safe moves when they satisfy the goal: searches, filtered decks, tag renames, and small deck/tag cleanup.
- For destructive or broad changes, preview the exact affected cards/notes with counts, back up, and get explicit approval.

## Restraint

Deck structure is personal and tied to scheduling. Don't pitch a grand reorganization when the user asked to merge two tags. Renames and moves are cheap; deletions are not — anything destructive gets a backup and an explicit list first.
