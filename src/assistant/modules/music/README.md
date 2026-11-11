# music

Plays music from YouTube on the dashboard's speaker, without ads.

- **Source:** YouTube through `yt-dlp` (`client.py`). `/play X` searches, queues the first
  result and then up to 25 tracks of its YouTube Mix (radio). A playlist link queues the
  playlist.
- **No ads:** the dashboard plays the plain audio stream (`/api/music/stream/{id}` redirects to
  YouTube's own m4a URL). YouTube's player, which is what inserts the ads, is never loaded.
- **Playback:** the queue is `music_queue` (lowest id = now playing). The dashboard polls
  `/api/music/state` every 2 s, plays the track and POSTs `/api/music/next/{id}` when it ends.
  The volume drops to 15% while the alarm, a briefing, "leave now" or the voice service talks.
- **Control:** Telegram `/play` `/pause` `/skip` `/stop`. Voice: "play …", "pause", "resume",
  "next song", "stop the music" (or just "stop" when no alarm is ringing), "what's playing".
- **Schedule / config:** none.

## Fragility

- YouTube changes often: if search or playback breaks, update yt-dlp first (`uv lock -P yt-dlp`).
- yt-dlp warns that YouTube without a JS runtime is deprecated. It works without one today; if
  formats go missing, install `deno` in the image (yt-dlp finds it on PATH).
- Stream URLs only work from the IP that resolved them. The API and the kiosk browser are on
  the same box, so that's fine; a dashboard opened from another network wouldn't play.
- A track that won't play is skipped. If YouTube refuses every track, the queue empties.
