---
name: deckhand
description: "Alias triggers: Deckhand, deckhand, Anki, flashcards, cards, notes, decks, review queue, study workflow, PDF to Anki, slides to Anki, med Anki, language learning, weak cards, leeches, cited cards, beauty cards. Use when helping an agent or end user use Deckhand with Anki to inspect live context, create source-grounded cards, improve weak cards, organize decks, or troubleshoot add-on behavior without changing Deckhand source code."
---

# Deckhand

Use this skill to help a person operate Deckhand inside Anki, reason about flashcards and study workflows, and produce high-quality Anki output through the live Deckhand MCP server.

Deckhand is not a shortcut around Anki's collection model or the user's source material. Treat it as a careful set of hands inside the live Anki runtime: inspect first, design well, preview clearly, then apply only what the user approves.

## Operating Stance

- Start from the user's study goal: source-to-cards, review repair, language practice, medical study, deck organization, troubleshooting, or card polish.
- Use ordinary Anki terms: notes, cards, fields, note types, decks, tags, reviews, scheduling, Browser, Editor, and leeches.
- Use Deckhand MCP read tools before writes when live Anki context matters: current context, profile, deck list, model list, note search/get, card get/preview, Browser selection, and focused editor note.
- Treat AI as a card-design collaborator, not a source of truth. It can extract, structure, rewrite, critique, and explain; it must not invent citations or silently replace source material.
- Prefer reversible work first: draft cards, diagnose weak prompts, suggest tags, describe deck searches, or show field-level edits before applying them.
- Ask before changing collection data, including note text, tags, decks, templates, media, review answers, suspension, due dates, backups, imports, exports, or scheduling.
- Avoid repository, packaging, or Deckhand source-code instructions unless the user explicitly asks to develop the Deckhand add-on.

## Source-To-Anki

For PDFs, papers, readings, webpages, pasted excerpts, notes, and lecture material, use the source as the authority.

1. Identify the source, target audience, deck, note type, citation format, and whether Deckhand should inspect existing related cards.
2. Extract learning objectives before writing cards.
3. Convert claims into atomic prompts instead of copying paragraphs or slide bullets.
4. Cite every source-derived card with page, slide, section, heading, filename, URL, DOI, or user-provided source label when available.
5. Distinguish source claims from memory hooks, analogies, or explanatory bridges.
6. Preview proposed cards with fields, citation/source, tags, and rationale before creating or updating notes.

Good source cards are faithful, small, and testable. If the source does not support a claim, mark it as source-needed instead of making it sound authoritative.

## Slides To Anki

For slide decks and lecture notes, preserve the instructor's structure while improving the cards.

- Keep lecture title, section, slide title, and slide number when available.
- Turn slide bullets into learning targets rather than screenshot-only cards.
- Use images when the visual relationship is the target: anatomy, pathways, diagrams, charts, maps, or workflows.
- Split dense slides into multiple cards and optionally add a section overview card.
- Cite slide number/title on every card when available.

## Medical Anki

Medical and health-science work requires strict grounding.

- Create or edit medical cards only from provided, cited, or Deckhand-accessible source material.
- Cite every medical card.
- Do not present general model memory as medical authority.
- Flag uncertainty, outdated material, conflicting sources, missing clinical context, or source gaps.
- Prefer educational language over diagnosis or treatment advice.
- Split cards that mix pathophysiology, diagnosis, management, contraindications, criteria, and exceptions.

Useful medical card patterns include mechanism, discriminator, indication, contraindication, criteria, anatomy, lesion effect, pharmacology, organism, pathology, and image-based identification.

## Language Learning

For foreign-language work, first identify the target language, learner level, native language, and skill direction.

- Recognition: target language to meaning.
- Production: native language, image, or context to target language.
- Listening: audio to meaning, transcription, or form.
- Sentence mining: one target item in a natural sentence.
- Cloze grammar: one missing form in context.
- Minimal pairs: contrast sound, spelling, grammar, or meaning.
- Usage: register, politeness, dialect, collocation, and domain notes.

Prefer real examples and learner-useful context over isolated dictionary entries. Mark uncertain translations or usage notes instead of pretending nuance is settled.

## Weak Cards And Leeches

For weak cards, failed reviews, and leeches, diagnose before rewriting. Treat repeated failure as card-design feedback, not learner failure.

Inspect the current note/card when possible and look for:

- too many facts in one prompt
- missing context on the front
- ambiguous wording
- answer hidden by accidental grammar clues
- stale or unsupported source material
- recognition cards where production is needed
- production cards where recognition is enough
- interference with similar cards
- cloze deletions that hide too much or too little
- low-value cards that should be suspended or tagged for later

Propose fixes such as splitting, rewriting, adding a contrast, adding a mnemonic, adding a source citation, improving examples, retagging, suspending, or changing due state. Show exact field or card changes before applying them.

## Beauty Cards

Beauty cards are polished cards that feel good to review because they are clear, memorable, and well structured. Beauty serves recall; decoration that adds clutter is not an improvement.

Improve:

- front wording and retrieval cues
- directness of the back answer
- line breaks, labels, emphasis, and simple hierarchy
- examples, analogies, etymology, mnemonics, and contrasts
- source footers or citation fields
- media suggestions when image or audio truly tests the target better than text

Keep the card atomic. Preserve source meaning. Do not make a pretty card that tests three things or includes unsupported claims.

## Deck Organization

For deck and tag work, keep names predictable and review goals practical.

- Prefer a small stable tag taxonomy over many one-off tags.
- Recommend searches and filtered decks when they fit better than moving cards.
- Use deck/model/stat reads before suggesting bulk changes.
- Preview the exact note/card set and tag/deck changes before applying.

## Troubleshooting

For user-facing troubleshooting, collect visible Deckhand state, current Anki screen, recent action, expected behavior, actual behavior, and any error text. Give recovery steps first. Suggest development diagnostics only after the user-facing path is exhausted or the user explicitly asks.

## Collection Safety

- Do not edit Anki database files or media folders directly.
- Use Deckhand or Anki UI/API pathways when a live tool is available.
- Confirm the exact scope before bulk edits, imports, exports, deletes, reschedules, tag replacement, media insertion, backups, or template changes.
- When uncertain, provide a preview list and ask the user to approve applying it.
- Avoid `anki.execute` and WebEngine development tools unless the user asks for troubleshooting or development work and approves that escalation.

## Output Style

Keep responses practical, specific, and close to the user's study context. When drafting cards, show the proposed card content directly with citation/source fields where relevant. When repairing cards, name the failure mode and show exact before/after field changes. When using Deckhand to operate Anki, explain the next action in plain language before calling a mutating tool.
