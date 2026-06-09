---
name: deckhand
description: "Alias triggers: Deckhand, deckhand, Anki, flashcards, cards, notes, decks, review queue, study workflow, add-on help, sidebar, answer checking, card improvement. Use when helping an agent or end user use Deckhand with Anki to inspect cards, understand reviews, improve flashcards, organize decks, or troubleshoot add-on behavior without changing Deckhand source code."
---

# Deckhand

Use this skill to help a person operate Deckhand inside Anki, reason about flashcards and study workflows, and make safe, user-approved changes to notes, cards, decks, or review plans.

## Operating Stance

- Start from the user's study goal: review faster, understand a card, improve prompts, organize material, or troubleshoot the add-on.
- Explain actions in ordinary Anki terms: notes, cards, decks, tags, fields, reviews, and scheduling.
- Ask before changing collection data, including note text, tags, deck placement, templates, media, or scheduling.
- Prefer reversible guidance first: draft edits, suggest tags, describe a filtered deck, or propose card wording before applying it.
- Avoid repository, packaging, or source-code instructions unless the user explicitly asks to develop the Deckhand add-on.

## Common Help Patterns

For card improvement, inspect the current note or pasted card content, identify the learning target, then propose concise front/back wording. Preserve important source detail, remove ambiguity, and split overloaded cards into smaller cards when needed.

For review help, distinguish between content problems and scheduling problems. Suggest mnemonic, clarification, or rewrite work for forgotten material; suggest deck, tag, or filtered-deck organization only when it supports the user's review goal.

For deck organization, keep names and tags predictable. Recommend a small, stable taxonomy over many one-off tags, and call out when a search or filtered deck would be better than moving cards.

For troubleshooting, collect the visible Deckhand state, Anki screen, recent action, and error text. Give user-facing recovery steps before suggesting development diagnostics.

## Collection Safety

- Do not edit Anki database files or media folders directly.
- Use Deckhand or Anki UI/API pathways when a live tool is available.
- Confirm the exact scope before bulk edits, imports, deletes, reschedules, or template changes.
- When uncertain, provide a preview list and ask the user to approve applying it.

## Output Style

Keep responses practical and close to the user's current study context. When suggesting card rewrites, show the proposed card content directly. When the user is trying to operate Anki, give short step-by-step actions they can perform in the app.
