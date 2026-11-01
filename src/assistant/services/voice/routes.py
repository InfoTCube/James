"""What the voice service is doing, for the dashboard's full-screen voice overlay.

The voice service POSTs every change (listening + mic level + live transcript, thinking,
speaking + the line being read, idle); the dashboard gets them as server-sent events.
Kept in memory: it only matters while someone is talking.
"""

import asyncio
import json
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

router = APIRouter()


class VoiceState(BaseModel):
    state: Literal["idle", "listening", "thinking", "speaking"]
    text: str = Field("", max_length=2000)  # what you said (so far, while listening)
    reply: list[str] = Field([], max_length=20)  # the answer's lines
    line: int = -1  # the line being read
    level: float = Field(0, ge=0, le=1)  # mic loudness while listening


_state, _version = VoiceState(state="idle"), 0
STREAM_FOR = 300  # seconds; then the browser reconnects (a server restart never waits on us)


@router.post("/api/voice/state")
def set_state(state: VoiceState) -> dict:
    global _state, _version
    _state, _version = state, _version + 1
    return {"ok": True}


@router.get("/api/voice/events")
async def events() -> StreamingResponse:
    async def stream():
        seen = -1
        for i in range(STREAM_FOR * 20):
            if seen != _version:
                seen = _version
                yield f"data: {json.dumps(_state.model_dump())}\n\n"
            elif i % 300 == 0:
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.05)

    return StreamingResponse(stream(), media_type="text/event-stream")
