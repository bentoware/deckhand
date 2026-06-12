# deckhand check

Quality-review cards when quality is the user's intent: find what will make cards fail, interfere, mislead, or waste reviews, then propose useful fixes. Do not hijack study intent: if the user wants to review due cards, help them get into Anki's normal review flow instead.

## Intent-first recipe

- Infer the quality question: a deck, tag, Browser selection, Anki search, newly drafted cards, leeches, or "these feel bad". If scope is unclear but live context suggests one, propose it instead of stalling.
- Size the pass and choose a useful depth. By default: up to ~50 cards in scope, check every card; for larger scopes, check likely pain first (leeches, high-lapse cards, newly changed material), then a random sample of ~30 of the rest to estimate overall quality. Report findings *and* the estimated rate ("checked 30 of 800; 6 had majors — expect roughly 1 in 5 deck-wide"), then offer to continue in batches.
- Read compactly using [../references/runtime.md](../references/runtime.md). Pull only needed fields and review signals; render or screenshot only when visual card behavior matters.
- Judge with [../references/rubric.md](../references/rubric.md), and use [../references/domains.md](../references/domains.md) when medical, language, or slide context changes the bar.
- Report in plain language: what was checked, what matters most, why it matters for studying, and exact before/after fixes when possible.
- Offer to apply fixes, but do not mutate without approval.

## Tone

Findings are about cards, not about the user. Most weak cards are normal first drafts. A clean check deserves saying so: "checked 40 cards, no blockers, two minor polish suggestions" builds more trust than inventing problems.
