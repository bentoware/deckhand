# Security

Deckhand can inspect and mutate an Anki collection, so treat safety issues seriously.

Please avoid publishing exploit details before a fix is available. Report suspected issues privately to the maintainers, including reproduction steps, affected Anki/OS versions, and whether the issue can mutate notes, cards, media, files, or agent configuration.

High-signal areas:

- approval bypasses for collection or filesystem mutations
- unsafe command execution paths
- bridge authentication or local transport exposure
- accidental leakage of card contents, media, or user profile paths
- package boundary mistakes that ship private or unrelated code
