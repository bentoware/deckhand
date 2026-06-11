# deckhand fix

Repair weak cards and leeches. Repeated failure is card-design feedback, not learner failure — diagnose before rewriting, and name the failure.

## Flow

1. **Find the patients.** Three entry points:
   - the user names cards ("this card", a Browser selection, a deck/tag);
   - failure data: leeches (`tag:leech`) and high-lapse cards (`prop:lapses>=4`) in the user's decks — when the user just says "fix my leeches", start here and report how many you found;
   - a handoff from `check` with findings already in hand.
2. **Diagnose each card.** Read the actual note (compactly — see [../references/runtime.md](../references/runtime.md)) plus its review stats (lapses, ease, interval). Name the failure mode from [../references/rubric.md](../references/rubric.md) — the diagnosis section lists the usual culprits for lapses. Check for interference: search for sibling cards with similar prompts before concluding the card is fine.
3. **Propose the fix.** Match fix to failure: split a non-atomic card, add front context, rephrase an ambiguous prompt, add a contrast card for interference, re-aim a wrong-direction card, re-scope a cloze, add a citation, or — legitimately — suspend or tag a low-value card for later. Show exact before/after field text for every card. If a fix needs source material you don't have, say so and ask rather than inventing content.
4. **Apply on approval.** Backup first when touching more than a handful of notes. Apply, confirm counts, `mw.reset()`. If cards were split, tell the user the new cards start unscheduled — that's expected.

## Scheduling honesty

Fixing a card's text doesn't fix its review history. Offer, but never silently do, scheduling changes (forget/reposition, unsuspend, due-date moves) — and explain the consequence in one plain sentence before the user decides.
