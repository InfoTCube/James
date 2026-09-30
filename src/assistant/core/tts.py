"""Text to speech with Piper (offline neural voices). Used by the dashboard now, voice later.

Voices are downloaded once from Hugging Face (rhasspy/piper-voices) into data/voices.
"""

import io
import logging
import re
import threading
import wave
from functools import lru_cache
from pathlib import Path

import httpx
from piper import PiperVoice

from assistant.core.config import fold
from assistant.core.db import DB_PATH

log = logging.getLogger(__name__)

VOICES_DIR = DB_PATH.parent / "voices"
VOICES_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
_lock = threading.Lock()  # one synthesis at a time: it's CPU-bound anyway


def _voice_url(name: str) -> str:
    """en_GB-cori-medium → .../en/en_GB/cori/medium/en_GB-cori-medium.onnx"""
    lang, speaker, quality = name.split("-")
    return f"{VOICES_URL}/{lang.split('_')[0]}/{lang}/{speaker}/{quality}/{name}.onnx"


def download_voice(name: str) -> Path:
    """The voice's model file, downloaded on first use."""
    model = VOICES_DIR / f"{name}.onnx"
    for path, url in (
        (model, _voice_url(name)),
        (Path(f"{model}.json"), f"{_voice_url(name)}.json"),
    ):
        if not path.exists():
            log.info("downloading voice %s", path.name)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".part")
            with httpx.stream("GET", url, follow_redirects=True, timeout=120) as r:
                r.raise_for_status()
                with tmp.open("wb") as f:
                    for chunk in r.iter_bytes():
                        f.write(chunk)
            tmp.replace(path)  # never leave a half-downloaded model behind
    return model


@lru_cache(maxsize=2)
def _load(name: str) -> PiperVoice:
    return PiperVoice.load(download_voice(name))


def synthesize(text: str, voice: str) -> bytes:
    """Speech for `text` as WAV bytes."""
    buf = io.BytesIO()
    with _lock, wave.open(buf, "wb") as wav:
        _load(voice).synthesize_wav(text, wav)
    return buf.getvalue()


# --- mixed English/Polish: Polish names read by a Polish voice ---------------------------------

POLISH_LETTERS = set("ąćęłńóśźż")
# ponytail: spelling heuristic; words it misses (e.g. "Praca") are read by the English voice.
POLISH_SPELLING = re.compile(
    r"(szcz|sz|cz|rz|dz|ść|ię)|(anie|enie|cji|acja|ego|ych|ymi|owa|owy|owe|ski|ska|cki|cka|ów)$"
)
# words of our own English sentences that the checks above would misread as Polish
ENGLISH = {"tomorrow", "arrives", "overcast", "showers", "drizzle", "degrees", "birthday"}
ABBREVIATIONS = {"pl": "plac", "ul": "ulica", "al": "aleja", "os": "osiedle"}


def is_polish(word: str, lexicon: set[str]) -> bool:
    w = word.lower()
    if not w.isalpha() or w in ENGLISH:
        return False
    return bool(POLISH_LETTERS & set(w)) or fold(w) in lexicon or bool(POLISH_SPELLING.search(w))


def split_languages(text: str, lexicon: set[str]) -> list[tuple[str, bool]]:
    """Text → [(chunk, is_polish)], consecutive words of one language kept together.
    Spaces and punctuation stay with the chunk they follow."""
    # "Mateusz's birthday" → "Mateusz birthday": a Polish voice can't do English possessives
    text = re.sub(r"(\w+)['’]s\b", lambda m: m[1] if is_polish(m[1], lexicon) else m[0], text)
    chunks: list[list] = []
    for i, part in enumerate(re.split(r"(\w+)", text)):
        if not part:
            continue
        polish = is_polish(part, lexicon) if i % 2 else None  # odd parts are words
        if chunks and (polish is None or polish == chunks[-1][1]):
            chunks[-1][0] += part
        elif polish is None:
            chunks.append([part, False])
        else:
            chunks.append([part, polish])
    return [(c, p) for c, p in chunks]


def _for_polish_voice(chunk: str) -> str:
    """ALL-CAPS stop names read as words, and abbreviations spelled out: "PL." → "plac"."""
    return re.sub(r"\b(pl|ul|al|os)\.", lambda m: ABBREVIATIONS[m[1]], chunk.lower())


def synthesize_mixed(text: str, voice: str, polish_voice: str, lexicon: set[str]) -> bytes:
    """Like synthesize(), with the Polish words read by `polish_voice`."""
    chunks = [(c, pl) for c, pl in split_languages(text, lexicon) if any(ch.isalnum() for ch in c)]
    if not any(pl for _, pl in chunks):
        return synthesize(text, voice)
    frames, params = [], None
    for chunk, polish in chunks:
        wav = synthesize(
            _for_polish_voice(chunk) if polish else chunk, polish_voice if polish else voice
        )
        with wave.open(io.BytesIO(wav)) as w:
            if params and w.getframerate() != params.framerate:
                return synthesize(text, voice)  # voices can't be joined: English only
            params = params or w.getparams()
            frames.append(w.readframes(w.getnframes()))
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setparams(params)
        w.writeframes(b"".join(frames))
    return out.getvalue()
