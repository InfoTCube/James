"""Notes: save, search, delete. Used by the bot now and voice later. No network here."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant.core.config import fold
from assistant.modules.notes.models import Note


def add_note(session: Session, text: str, now: datetime) -> Note:
    """Save a note."""
    note = Note(text=text.strip(), folded=fold(text), created_at=now)
    session.add(note)
    session.flush()  # assigns the id
    return note


def search_notes(session: Session, query: str = "", limit: int = 10) -> list[Note]:
    """Newest notes containing every word of `query` (case and Polish diacritics ignored).
    Empty query: the newest notes.

    ponytail: LIKE scan, fine for thousands of notes; SQLite FTS5 if it ever gets slow.
    """
    q = select(Note)
    for word in fold(query).split():
        escaped = word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        q = q.where(Note.folded.like(f"%{escaped}%", escape="\\"))
    return list(session.scalars(q.order_by(Note.id.desc()).limit(limit)))


def delete_note(session: Session, note_id: int) -> Note | None:
    """Delete a note; returns it, or None if there's no such note."""
    note = session.get(Note, note_id)
    if note:
        session.delete(note)
        session.flush()  # a second delete in the same transaction then finds nothing
    return note
