"""Voice: wake word → speech to text → intent → spoken reply, on the local mic and speaker.

    python -m assistant.services.voice

Say the wake word (`listening.wake_word`, "hey jarvis"; a trained one works too, see
config.yaml), wait for the beep, ask. It records until you stop talking, transcribes with
faster-whisper (base.en, CPU), answers with `intents.answer` and reads the reply with Piper.
The dashboard shows it all full screen (ASSISTANT_API_URL, default http://localhost:8000):
your loudness and words as you speak, then the answer line by line as it's read.
Models download on first start into data/ (wake word ~5 MB, whisper ~150 MB).
Tuning knobs: `listening:` in config.yaml.
"""

import io
import logging
import os
import threading
import wave
from pathlib import Path

import httpx
import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
from openwakeword import Model
from openwakeword.utils import download_models
from sqlalchemy.orm import Session

from assistant.core import tts
from assistant.core.config import CONFIG_PATH, Config, fold, load_config
from assistant.core.db import DB_PATH, get_engine, utcnow
from assistant.modules.mpk import service as mpk
from assistant.services.voice.intents import SORRY, answer, spoken

log = logging.getLogger("assistant.voice")

RATE = 16000  # what openWakeWord and whisper expect
CHUNK = 1280  # 80 ms, openWakeWord's frame
END_QUIET = 1.0  # seconds of quiet after speech: you've finished
NO_SPEECH = 3.0  # seconds of quiet with no speech at all: give up
MAX_SECONDS = 10
LOUD = 5000  # mic level (RMS) shown as the biggest orb on the dashboard


class Screen:
    """Tells the dashboard what we're doing (POST /api/voice/state). Sent from its own thread so
    a slow or missing dashboard never stalls the mic; only the newest state is sent."""

    def __init__(self, url: str) -> None:
        self.url, self.state, self.changed = url, {"state": "idle"}, threading.Event()
        self.lock = threading.Lock()  # the mic loop and the live transcript both update
        threading.Thread(target=self._send, daemon=True).start()

    def set(self, state: str, **fields) -> None:
        with self.lock:
            self.state = {"state": state, **fields}
        self.changed.set()

    def update(self, **fields) -> None:
        with self.lock:
            self.state = {**self.state, **fields}
        self.changed.set()

    def _send(self) -> None:
        with httpx.Client(timeout=2) as client:
            while True:
                self.changed.wait()
                self.changed.clear()
                try:
                    client.post(self.url, json=self.state)
                except httpx.HTTPError:
                    pass  # no dashboard: voice works without it


def load_wake_word(name: str) -> Model:
    """A built-in openWakeWord model by name (downloaded), or a trained .onnx next to config.yaml.
    A missing trained one falls back to hey_jarvis."""
    folder, custom = DB_PATH.parent / "wakeword", CONFIG_PATH.parent / name
    if name.endswith(".onnx") and not custom.exists():
        log.warning("wake word model %s not found (see listening: in config.yaml), "
                    "using hey_jarvis", custom)  # fmt: skip
        name = "hey_jarvis"
    # the feature models always, plus the named built-in one; skips files it already has
    download_models([Path(name).stem], str(folder))
    model = custom if name.endswith(".onnx") else max(folder.glob(f"{name}_v*.onnx"))  # newest
    return Model(
        wakeword_models=[str(model)],
        inference_framework="onnx",
        melspec_model_path=str(folder / "melspectrogram.onnx"),
        embedding_model_path=str(folder / "embedding_model.onnx"),
    )


def heard_wake_word(mic: sd.InputStream, wake: Model, threshold: float) -> bool:
    """Reads the next 80 ms from the mic: was it the end of the wake word?"""
    chunk, _ = mic.read(CHUNK)
    if max(wake.predict(chunk[:, 0]).values()) >= threshold:
        wake.reset()  # its buffers would fire again on the same audio
        return True
    return False


def to_float(chunks: list[np.ndarray]) -> np.ndarray:
    return np.concatenate(chunks).astype(np.float32) / 32768


def transcribe(whisper: WhisperModel, audio: np.ndarray, quick: bool = False) -> str:
    segments, _ = whisper.transcribe(
        audio, language="en", vad_filter=True, beam_size=1 if quick else 5
    )  # vad: no "Thank you." from silence
    return " ".join(s.text.strip() for s in segments).strip()


def live_transcript(
    whisper: WhisperModel, chunks: list, done: threading.Event, screen: Screen
) -> None:
    """While recording: transcribe what's been said so far, over and over, for the dashboard."""
    # ponytail: re-transcribes the whole recording each time (≤10 s, ~1 s on CPU); a streaming
    # recogniser if the mini PC lags
    seen = 0
    while not done.wait(0.1):
        if len(chunks) - seen < 6:  # less than ~0.5 s of new audio
            continue
        seen = len(chunks)
        text = transcribe(whisper, to_float(chunks[:seen]), quick=True)
        if not done.is_set():
            screen.update(text=text)


def record(mic: sd.InputStream, silence_level: int, whisper: WhisperModel, screen: Screen):
    """Until END_QUIET of quiet after speech (NO_SPEECH if nothing is said, MAX_SECONDS at most),
    as float32 for whisper. Shows the loudness and the words so far on the dashboard."""
    chunks, quiet, peak, done = [], 0, 0, threading.Event()
    live = threading.Thread(target=live_transcript, args=(whisper, chunks, done, screen))
    live.start()
    try:
        for _ in range(MAX_SECONDS * RATE // CHUNK):
            chunk, _ = mic.read(CHUNK)
            chunks.append(chunk[:, 0])
            level = int(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))
            peak = max(peak, level)
            screen.update(level=round(min(1, level / LOUD) ** 0.5, 2))
            quiet = 0 if level > silence_level else quiet + 1
            if quiet * CHUNK >= RATE * (END_QUIET if peak > silence_level else NO_SPEECH):
                break
    finally:
        done.set()
    live.join()  # one whisper run at a time
    log.info("recorded %.1f s, loudest %d (silence_level %d)",
             len(chunks) * CHUNK / RATE, peak, silence_level)  # fmt: skip
    return to_float(chunks)


def beep() -> None:
    t = np.arange(int(0.15 * RATE)) / RATE
    sd.play(0.3 * np.sin(2 * np.pi * 880 * t).astype(np.float32), RATE)
    sd.wait()


def say(
    text: str,
    config: Config,
    lexicon: set[str],
    screen: Screen,
    wake: Model,
    heard: str = "",
    one_language: bool = False,
) -> bool:
    """Read `text` aloud line by line, the dashboard following along. Polish names in our
    sentences are read by the Polish voice; with `one_language` (headlines, titles) each line
    is read whole by the voice of its language. Saying the wake word cuts it off: True then."""
    synthesize = tts.synthesize_line if one_language else tts.synthesize_mixed
    lines = spoken(text)
    with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=CHUNK) as mic:
        for i, line in enumerate(lines):
            wav = synthesize(line, config.voice, config.voice_pl, lexicon)
            screen.set("speaking", text=heard, reply=lines, line=i)
            with wave.open(io.BytesIO(wav)) as w:
                sd.play(np.frombuffer(w.readframes(w.getnframes()), np.int16), w.getframerate())
            while sd.get_stream().active:
                if heard_wake_word(mic, wake, config.listening.wake_threshold):
                    sd.stop()
                    return True
    return False


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)  # a line per live transcript
    config = load_config()
    cfg = config.listening
    if cfg.device:
        sd.default.device = cfg.device
    screen = Screen(
        os.environ.get("ASSISTANT_API_URL", "http://localhost:8000") + "/api/voice/state"
    )
    wake = load_wake_word(cfg.wake_word)
    whisper = WhisperModel(
        "base.en", device="cpu", compute_type="int8", download_root=str(DB_PATH.parent / "whisper")
    )
    with Session(get_engine()) as session:  # Polish words for the voice: Wrocław stop names
        # ponytail: built once; stop names barely change and every deploy restarts us
        lexicon = {fold(w) for n in mpk.stop_names(session) for w in n.split() if len(w) > 2}
    log.info("listening for %r", cfg.wake_word)
    interrupted = False  # the wake word during an answer: listen again straight away
    while True:
        # closed while we think, so it doesn't pile up audio; say() listens for the wake word
        with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=CHUNK) as mic:
            while not interrupted and not heard_wake_word(mic, wake, cfg.wake_threshold):
                pass
            screen.set("listening", text="", level=0)
            beep()
            audio = record(mic, cfg.silence_level, whisper, screen)
        text, interrupted = "", False
        try:
            text = transcribe(whisper, audio)
            log.info("heard: %r", text)
            if text:
                screen.set("thinking", text=text)
                reply, one_language = answer(text, utcnow(), config)
                log.info("reply: %r", reply)
                interrupted = say(reply, config, lexicon, screen, wake, text, one_language)
        except Exception:  # a bad request must never stop us listening
            log.exception("voice request failed")
            try:
                interrupted = say(SORRY, config, lexicon, screen, wake, text)
            except Exception:
                log.exception("can't speak either")
        finally:
            if interrupted:
                log.info("interrupted")
            else:
                screen.set("idle")


if __name__ == "__main__":
    main()
