# Card quality rubric

Shared quality bar for `create`, `check`, and `fix`. A good card is **faithful** (says what the source says), **small** (one retrieval target), and **testable** (a clear prompt with one defensible answer). Every card Deckhand creates must pass this rubric before it is written; every card Deckhand checks is judged against it; every weak card Deckhand fixes gets its failure named from this catalog.

## Severity

- **blocker** — the card teaches something wrong or unverifiable. Never create it; flag it first when checking.
- **major** — the card will fail reviews or interfere with learning. Fix before creating; lead findings with these.
- **minor** — polish. Worth fixing in bulk or when touching the card anyway.

## Failure modes

### Blockers

- **unfaithful** — the card contradicts or overstates its source. The fix is to requote the source, not to soften the wording.
- **uncited-claim** — a source-derived card with no citation (page, slide, section, URL, DOI, or user-provided label). Medical and health-science cards are *always* blockers when uncited. If no source supports the claim, mark the card `source-needed` rather than letting it sound authoritative.
- **invented-authority** — model memory presented as source material: fabricated citations, "well-known fact" medical claims, settled-sounding translations of genuinely uncertain usage.

### Majors

- **non-atomic** — more than one fact behind one prompt. Symptom: the answer has "and" joints, or mixes mechanism with management, criteria with exceptions. Fix: split.
- **missing-context** — the front can't be answered without information that exists only in the answer or in the card's deck/tag placement ("What year?" — of what?). Fix: put the discriminating context on the front.
- **ambiguous-prompt** — several defensible answers; the learner can be "wrong" while being right. Fix: constrain the question until one answer wins.
- **format-leak** — grammar, blank length, article choice, or phrasing gives the answer away. The learner pattern-matches instead of recalling. Fix: rephrase so the surface form is uninformative.
- **interference** — two or more cards with near-identical prompts and different answers (sibling vocab, similar drugs, lookalike kanji). Fix: add an explicit contrast card or a discriminating cue on each front.
- **wrong-direction** — recognition card where the goal is production, or vice versa. Ask what the learner must *do* in the wild, then point the card that way.
- **bad-cloze** — deletion hides too much (whole clause: unanswerable) or too little (one trivial word: no retrieval). Fix: delete exactly the learning target.
- **prompt-dump** — a pasted paragraph or slide bullet with a question mark on it. Not a retrieval prompt. Fix: extract the claim and ask for it.
- **buried-answer** — the back opens with throat-clearing; the answer is in sentence three. Fix: answer first, elaboration after.

### Minors

- **stale-source** — material the source base has since revised or the user has flagged as outdated.
- **low-value** — true but not worth the reviews it costs. Propose suspend, tag for later, or delete — the user decides.
- **clutter** — decoration that doesn't serve recall: redundant images, emphasis on everything, three mnemonics where one would do. Beauty serves recall; remove what doesn't.
- **flat-structure** — a wall of text the eye can't parse in two seconds. Add line breaks, labels, simple hierarchy.
- **missing-hook** — a correct but forgettable card that would benefit from an example, contrast, etymology, or mnemonic. Mark hooks as hooks — never let a memory aid read like a source claim.
- **citation-format** — citation present but inconsistent with the deck's convention.

## Diagnosing weak cards

Repeated failure is card-design feedback, not learner failure. For a leech or high-lapse card, inspect the actual note and name the failure mode from the catalog above before proposing anything. The most common culprits for lapses, in rough order: non-atomic, missing-context, interference, ambiguous-prompt, wrong-direction. If no failure mode fits, consider low-value — suspending a bad card is a legitimate fix.

## What a finding looks like

When reporting against this rubric, each finding names the card, the failure mode, the evidence, and the exact proposed change:

> **major · non-atomic** — card 1781219991487 ("What are the symptoms and treatment of X?")
> Front asks for two targets; lapse count 6.
> Proposed: split into "symptoms of X" and "first-line treatment of X", each citing p. 142.

Show before/after field text for every proposed edit. Never apply fixes without the user seeing the list first.
