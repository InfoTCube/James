from fastapi import APIRouter, HTTPException
from fastapi import Path as PathParam
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from assistant.core.db import get_engine
from assistant.modules.music import client, service

router = APIRouter()
VideoId = PathParam(pattern=r"^[\w-]{11}$")  # goes into a YouTube URL: nothing else allowed


@router.get("/api/music/state")
def state() -> dict:
    """What the dashboard should be playing."""
    with Session(get_engine()) as session:
        t = service.now_playing(session)
        if t is None:
            return {"video_id": None}
        return {"video_id": t.video_id, "title": t.title, "paused": t.paused}


@router.post("/api/music/next/{video_id}")
def next_track(video_id: str = VideoId) -> dict:
    """The dashboard finished (or couldn't play) `video_id`."""
    with Session(get_engine()) as session, session.begin():
        service.skip(session, video_id)
    return {"ok": True}


@router.get("/api/music/stream/{video_id}")
def stream(video_id: str = VideoId) -> RedirectResponse:
    """Redirect to the track's audio on YouTube's servers; the browser streams it from there."""
    try:
        return RedirectResponse(client.stream_url(video_id))
    except Exception as e:
        raise HTTPException(502, f"YouTube refused: {type(e).__name__}") from e
