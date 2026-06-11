# Avatar Assets

Avatar candidates for Deckhand and Bentoware.

- `deckhand-repo-avatar.png`: 1024px GitHub repository avatar for `bentoware/deckhand`.
- `deckhand-repo-avatar-512.png`: 512px copy for smaller upload targets.
- `bentoware-org-avatar.png`: 1024px GitHub organization avatar for `bentoware`.
- `bentoware-org-avatar-512.png`: 512px copy for smaller upload targets.
- `avatar-preview-strip.png`: side-by-side preview only.

The Deckhand avatar is a square crop of the human deckhand from `../deckhand-hero.jpg`, framed for GitHub's circular repository avatar display. The Bentoware avatar keeps the same family style but uses a broader compass/code-bracket studio mark so it can stand apart from the repo.

Deckhand crop source:

```sh
magick ../deckhand-hero.jpg -crop 520x520+230+0 +repage -resize 1024x1024 -strip deckhand-repo-avatar.png
```

Bentoware generation prompt:

```text
Square GitHub organization avatar for Bentoware. Create a refined nautical software studio mark connected to the Deckhand visual world but not repo-specific: a brass compass star and subtle bent/curved code bracket motif worked into a circular rope-and-ink badge, parchment chart background, tiny navy waves at the bottom. Vintage nautical engraving, navy ink, brass accents, muted red pin detail. It should feel like the maker/parent brand for thoughtful developer tools. Clear at small size. No text, no letters, no watermark, no cat, no humans, no extra animals.
```
