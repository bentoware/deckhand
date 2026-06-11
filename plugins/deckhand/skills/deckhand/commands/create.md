# deckhand create

Turn source material — PDFs, slides, lecture notes, webpages, pasted excerpts, or a named topic — into cited, rubric-checked Anki cards. The source is the authority; cards are faithful, small, and testable.

## Flow

1. **Intake.** Establish: the source (file, paste, URL, or topic), the target audience and their level, the destination deck, the note type, the citation convention, and whether to inspect existing related cards first. Ask only for what you can't infer; one round of questions, not an interrogation.
2. **Inspect.** Look at the live collection (see [../references/runtime.md](../references/runtime.md)): does the deck exist, what note type fits, are there existing cards on this material that new cards would duplicate or interfere with?
3. **Extract learning objectives** from the source before writing any card. Objectives, not paragraphs, become cards.
4. **Draft.** Convert claims into atomic prompts. Cite every source-derived card (page, slide, section, heading, filename, URL, DOI, or the user's source label). Distinguish source claims from memory hooks — mnemonics and analogies are welcome but must read as hooks, never as facts. If the source doesn't support a claim, mark it `source-needed` instead of making it sound authoritative.
5. **Self-check.** Run every draft against [../references/rubric.md](../references/rubric.md) before showing it. Fix blockers and majors; mention any minors you deliberately kept. This is the same check the `check` verb runs on existing cards — new cards are not exempt.
6. **Preview.** Show the proposed cards with fields, citation, tags, and a one-line rationale each. State the count and destination deck.
7. **Apply on approval.** Create the notes, confirm the count, and call `mw.reset()` so the user sees them. For large batches, back up first.

## Domain material

For medical/health-science, language-learning, or slide-deck sources, read [../references/domains.md](../references/domains.md) first — these have hard rules (medical cards are never uncited) and proven card patterns.

## Quality over quantity

Fewer good cards beat many mediocre ones. If the user asks for "all" of a large source, propose a scoped first batch (one chapter, one lecture) and iterate. Never pad output with low-value cards to look productive.
