# deckhand check

Quality-review cards the way engineers review code: scoped pass, named findings, proposed fixes, applied only on approval. This is *not* studying — if the user wants to review due cards, that happens in Anki, not here.

## Flow

1. **Scope.** What's being checked: a deck, a tag, an Anki search, the Browser selection, or "the cards we just made". If the user is vague, propose a scope from live context and confirm it.
2. **Size the pass.** Count the scoped cards first.
   - **≤ ~50 cards**: check every card.
   - **Larger scopes — triage, don't boil the ocean.** Check in priority order: (a) worst offenders by review data — leeches (`tag:leech`) and high-lapse cards (`prop:lapses>=4`) in scope; (b) a random sample of ~30 of the rest to estimate overall quality. Report findings *and* the estimated rate ("checked 30 of 800; 6 had majors — expect roughly 1 in 5 deck-wide"), then offer to continue in batches.
3. **Read compactly.** Pull only needed fields, stripped and truncated (see [../references/runtime.md](../references/runtime.md)). For template/styling concerns, render a sample card as text or screenshot one — don't dump HTML.
4. **Judge against the rubric.** Every finding names a failure mode from [../references/rubric.md](../references/rubric.md) with severity, evidence, and an exact proposed change. Read the domain rules in [../references/domains.md](../references/domains.md) when the scope is medical, language, or slide material.
5. **Report.** Lead with the summary (cards checked, blockers/majors/minors found), then findings ordered by severity. Use the finding format from the rubric: card, failure mode, evidence, proposed before/after. Plain language — the user may not know what a cloze is until you show them.
6. **Offer fixes.** Ask which findings to apply. On approval, hand off to the fix flow ([fix.md](fix.md)): backup if bulk, apply, confirm counts, `mw.reset()`.

## Tone

Findings are about cards, not about the user. Most "bad" cards are normal first drafts — say what's fixable and fix it. A clean check deserves saying so: "checked 40 cards, no blockers, two minor polish suggestions" builds more trust than inventing problems.
