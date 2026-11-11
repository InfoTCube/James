"""YouTube through yt-dlp: search (+ the video's YouTube Mix as radio) and a direct audio URL.

The dashboard plays the plain audio stream, not YouTube's player, so there are no ads.
"""

import logging
import re
import time
from functools import lru_cache

from yt_dlp import YoutubeDL

log = logging.getLogger(__name__)

MIX_SIZE = 25  # the Mix is endless; fetching 400+ entries takes ~15 s
FLAT = {"quiet": True, "no_warnings": True, "extract_flat": True, "playlistend": MIX_SIZE}
AUDIO = {"quiet": True, "no_warnings": True, "noplaylist": True,
         "format": "bestaudio[ext=m4a]/bestaudio"}  # fmt: skip

Track = tuple[str, str]  # (video id, title)


VIDEO_ID = re.compile(r"[\w-]{11}")


def _tracks(entries) -> list[Track]:
    """Videos only: a search also finds channels (UC…, 24 chars) and playlists."""
    return [
        (e["id"], e.get("title") or e["id"])
        for e in entries or []
        if VIDEO_ID.fullmatch(e.get("id") or "")
    ]


def find(query: str) -> list[Track]:
    """Tracks to queue: a playlist URL → its videos; a video URL or a search → that video, then
    its YouTube Mix. [] if nothing is found or YouTube fails."""
    try:
        with YoutubeDL(FLAT) as ydl:
            if query.startswith(("http://", "https://")):
                info = ydl.extract_info(query, download=False)
                if info.get("entries") is not None:  # a playlist
                    return _tracks(info["entries"])
                first = (info["id"], info.get("title") or info["id"])
            else:
                found = _tracks(ydl.extract_info(f"ytsearch5:{query}", download=False)["entries"])
                if not found:
                    return []
                first = found[0]
            try:
                url = f"https://www.youtube.com/watch?v={first[0]}&list=RD{first[0]}"
                mix = _tracks(ydl.extract_info(url, download=False).get("entries"))
            except Exception:  # still play the one we found
                log.exception("music: no mix for %s", first[0])
                mix = []
    except Exception:
        log.exception("music: search failed for %r", query)
        return []
    return [first, *(t for t in mix if t[0] != first[0])]


def stream_url(video_id: str) -> str:
    """A direct audio URL (valid ~6 h, only from this IP). Raises if YouTube refuses."""
    return _stream_url(video_id, int(time.time() // 3600))


@lru_cache(maxsize=64)
def _stream_url(video_id: str, _hour: int) -> str:
    with YoutubeDL(AUDIO) as ydl:
        return ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)[
            "url"
        ]
