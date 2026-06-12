# deckhand create

Turn the user's source, topic, goal, or rough idea into useful Anki cards. Default to faithful, small, testable, cited cards when source material exists, but do not make the user be a perfect prompt engineer: infer ordinary defaults, produce a useful preview, and let the user steer from there.

## Intent-first recipe

- Understand the desired end state: what the learner needs to remember or do, where the cards should land, and whether the user wants polished cards, a quick rough pass, or broad coverage.
- Ask only for missing details that materially affect the result: source/topic, level, target deck, note type, card direction, or safety-critical grounding. Otherwise choose a reasonable default and say what you assumed.
- Inspect the live collection when it will improve the outcome: destination deck, note type, existing related cards, duplicates, or current study context.
- Turn learning objectives into cards. Use the user's requested count as a goal, not a reason to pad low-value cards.
- Preview before writing. Show enough fields, citations or labels, tags, and rationale for the user to judge the result. Apply only after approval.

## Grounding and escape hatches

- Source-backed cards: cite page, slide, section, heading, filename, URL, DOI, or the user's source label.
- Low-risk practice cards without source material: allowed when useful, but label them as model-generated drafts or practice material and avoid pretending they are source-cited.
- Rough/fast mode: allowed when the user asks for speed or breadth. State the tradeoff, keep the preview honest, and offer a polish pass.
- Source-sensitive or authority-sensitive material, such as medical, legal, exam-specific, clinical, regulatory, or user-supplied course content: no fabricated authority. Without a source, offer a scaffold, question list, or `source-needed` draft rather than authoritative cards.
- Domain material: use [../references/domains.md](../references/domains.md) as a quality lens for medical, language-learning, and slide-deck material.

## Examples

- "Make me cards about the French Revolution" -> a topic with no source: draft labeled model-generated cards on the ordinary high-value points, preview a first batch, and offer to work from a real source if the user wants cited cards.
- "Make rough cards fast from this chapter" -> produce a clearly labeled rough preview and name what polish was deferred.
- "Turn these slides into cards" -> preserve slide structure and cite every card by slide.
