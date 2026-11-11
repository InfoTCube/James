"""The play queue. The dashboard plays its first track and asks for the next when it ends."""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from assistant.modules.music.models import MusicTrack


def now_playing(session: Session) -> MusicTrack | None:
    """The track playing (or paused) now; None when the queue is empty."""
    return session.scalar(select(MusicTrack).order_by(MusicTrack.id).limit(1))


def up_next(session: Session, limit: int = 5) -> list[MusicTrack]:
    """The tracks after the current one."""
    return list(session.scalars(select(MusicTrack).order_by(MusicTrack.id).offset(1).limit(limit)))


def play(session: Session, tracks: list[tuple[str, str]]) -> None:
    """Replace the queue with `tracks` (video id, title); the first starts right away."""
    stop(session)
    session.add_all(MusicTrack(video_id=v, title=t) for v, t in tracks)
    session.flush()


def skip(session: Session, video_id: str | None = None) -> MusicTrack | None:
    """Drop the current track (only if it's still `video_id`, so two dashboards ending the same
    track skip once); the new current one."""
    current = now_playing(session)
    if current and (video_id is None or current.video_id == video_id):
        session.delete(current)
        session.flush()
    return now_playing(session)


def stop(session: Session) -> bool:
    """Clear the queue; True if something was playing."""
    return session.execute(delete(MusicTrack)).rowcount > 0


def set_paused(session: Session, paused: bool) -> MusicTrack | None:
    """Pause or resume the current track; it, or None if nothing is queued."""
    current = now_playing(session)
    if current:
        current.paused = paused
    return current
