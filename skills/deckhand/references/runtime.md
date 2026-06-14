# Deckhand runtime notes

Deckhand exposes exactly three MCP tools. There are no typed card/note/deck tools — everything goes through these.

- `anki_runtime_info` — quick health check: is Anki running, which profile, collection open, current screen.
- `anki_backup_create` — native no-media backup to a folder you choose. Call before bulk edits, deletes, imports, template or scheduling changes.
- `anki_run_python` — runs your `code` **inside Anki's embedded Python interpreter** (the user never needs Python installed) **on the main Qt thread** (UI work is directly safe; long loops freeze the UI, so keep code short). Assign to a variable named `result` to return it.

## Keep output compact

Real notes are big: three notes of a 25-field model, truncated to 80 chars per field, is still ~5 KB. Always:

- `from anki.utils import strip_html` and truncate field values (`strip_html(v)[:80]`).
- Select only the fields you need; never return `note.items()` untouched for more than a handful of notes.
- For large dumps (full HTML, bulk exports, logs) pass `resultFilePath` to the tool, or use `deckhand.web` `file=` helpers, instead of returning data inline.

## Verified patterns

Search and read compactly:

```python
from aqt import mw
from anki.utils import strip_html
col = mw.col
nids = col.find_notes('deck:"My Deck" tag:foo')  # full Anki search syntax
note = col.get_note(nids[0])
fields = {k: strip_html(v)[:80] for k, v in note.items()}
```

Weak cards and leeches:

```python
leeches = col.find_cards("tag:leech")
struggling = col.find_cards("prop:lapses>=4")
card = col.get_card(cid)   # card.lapses, card.factor (ease*10), card.ivl (days)
```

Create a note (adds a native undo entry, "Add Note"):

```python
model = col.models.by_name("Basic")
note = col.new_note(model)
note["Front"] = "..."
note["Back"] = "..."
note.tags = ["my-tag"]
col.add_note(note, col.decks.id_for_name("Target Deck"))
```

Edit and bulk-edit (native undo entries unless `skip_undo_entry=True`):

```python
col.update_note(note)
col.update_notes(list_of_notes)
```

Preview a card as text — cheaper than a screenshot and usually enough:

```python
out = col.get_card(cid).render_output(browser=False)
strip_html(out.question_text), strip_html(out.answer_text)
```

After mutating while Anki's UI is open, call `mw.reset()` so the visible screen refreshes.

## deckhand.web — rendered UI when text is not enough

CDP-based driver for Anki's WebEngine pages. Use it when the *visual* result matters (templates, styling, image cards) or to drive UI the collection API can't reach.

```python
import deckhand.web as web
web.status()                      # is the CDP endpoint reachable
web.pages()                       # list debuggable pages
p = web.page(preferred="main")    # also "first", "strict", or target= selectors
p.text(max_chars=2000)            # visible text
p.snapshot(max_elements=200)      # compact element tree with stable uid refs
p.screenshot("/tmp/card.png")     # artifact metadata, not inline bytes
p.html(file="/tmp/page.html")     # write big HTML to file, never inline
p.eval("document.title")
p.click(uid=..., selector=..., text=..., x=..., y=...)
p.type("hello", selector="input")
p.press("Enter")
p.wait_for(selector=..., text=..., expression=...)
```

## Safety rails

- Never touch `collection.anki2` (SQLite) or the media folder directly; always go through `mw.col` / `aqt` APIs.
- Backup (`anki_backup_create`) before any bulk mutation; single-note edits are covered by Anki's native undo.
- Preview every proposed change to the user before applying it.
