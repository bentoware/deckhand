# deckhand organize

Decks, tags, duplicates, and filtered-deck suggestions. Keep names predictable and review goals practical; prefer the smallest reorganization that solves the actual problem.

## Flow

1. **Understand the pain.** "My decks are a mess" has many shapes: too many decks, orphaned tags, duplicates, can't find anything, or a review queue that mixes things that shouldn't mix. Ask what they're trying to *do* that the current structure prevents.
2. **Inspect first.** Read the live deck tree, tag list, and counts (see [../references/runtime.md](../references/runtime.md)) before proposing anything. Check for empty decks, near-duplicate tags (`pharm` vs `pharmacology`), and duplicate notes (same first field, `col.find_notes` with `dupe:` or field comparison).
3. **Propose the smallest change.**
   - Prefer a small stable tag taxonomy over many one-off tags.
   - Prefer searches and filtered decks over moving cards — a filtered deck is reversible and preserves scheduling; explain that in one sentence when suggesting it.
   - Consolidate tags by renaming, not retagging card-by-card.
   - For duplicates, show the pairs and let the user pick survivors — never auto-delete.
4. **Preview the exact set.** Before any bulk change, show precisely which notes/cards move, get retagged, or get deleted, with counts. Confirm scope, back up (`anki_backup_create`), apply, confirm counts, `mw.reset()`.

## Restraint

Deck structure is personal and tied to scheduling. Don't pitch a grand reorganization when the user asked to merge two tags. Renames and moves are cheap; deletions are not — anything destructive gets a backup and an explicit list first.
