"""Text to speech with Piper (offline neural voices). Used by the dashboard and the voice service.

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
# short Polish words the spelling checks miss (none of them English words)
POLISH_WORDS = {"w", "we", "na", "z", "ze", "od", "po", "dla", "przez", "oraz", "jest", "są",
                "nie", "się", "że", "już", "czy", "jak", "ma", "będzie", "został",
                # ...and calendar words (event titles are often Polish)
                "praca", "lekarz", "dentysta", "obiad", "trening", "urodziny", "basen", "mecz",
                "egzamin", "kolokwium", "wizyta", "zakupy", "impreza", "kino", "teatr", "fryzjer",
                "rodzina", "dom", "uczelnia", "kurs", "nauka", "wyjazd", "sprawdzian", "siatka",
                "projekt", "zebranie", "msza", "korepetycje"}  # fmt: skip
TIME = r"\b([01]?\d|2[0-3]):([0-5]\d)\b"  # 24 h, as we write them; "07.10" is a date


def _english_time(hour: str, minute: str) -> str:
    h, m = int(hour), int(minute)
    return f"{h % 12 or 12}{f':{m:02d}' if m else ''} {'AM' if h < 12 else 'PM'}"


MONTHS = "January February March April May June July August September October November December"
DAYS = {"Mon": "Monday", "Tue": "Tuesday", "Wed": "Wednesday", "Thu": "Thursday",
        "Fri": "Friday", "Sat": "Saturday", "Sun": "Sunday"}  # fmt: skip
DATE = rf"\b(?:({'|'.join(DAYS)}) )?(\d{{1,2}})\.(\d{{2}})\b(?!\.\d)"  # "Tue 06.10", "14.10"


def _english_date(m: re.Match) -> str:
    day, month = int(m[2]), int(m[3])
    if not (1 <= day <= 31 and 1 <= month <= 12):
        return m[0]  # a decimal number, not a date
    return f"{DAYS[m[1]] + ' ' if m[1] else ''}{day} {MONTHS.split()[month - 1]}"


def english_times(text: str) -> str:
    """ "08:00–16:00" → "from 8 AM to 4 PM", "07:40" → "7:40 AM", "Tue 06.10" → "Tuesday
    6 October": Piper's English voice reads "08:00" digit by digit, "16:30" as "sixteen-thirty"
    and "06.10" as a decimal."""
    text = re.sub(
        rf"{TIME}\s*[–-]\s*{TIME}",
        lambda m: f"from {_english_time(m[1], m[2])} to {_english_time(m[3], m[4])}",
        text,
    )
    return re.sub(DATE, _english_date, re.sub(TIME, lambda m: _english_time(m[1], m[2]), text))


def polish_times(text: str) -> str:
    """ "16:30" → "16 30", "08:00" → "8": read as "szesnaście trzydzieści", not digit by digit."""
    return re.sub(TIME, lambda m: f"{int(m[1])}" + (f" {m[2]}" if m[2] != "00" else ""), text)


ABBREVIATIONS = {"pl": "plac", "ul": "ulica", "al": "aleja", "os": "osiedle"}
ENGLISH_GLUE = {"the", "a", "an", "and", "of", "in", "on", "at", "to", "for", "from", "by",
                "with", "is", "are", "it", "your", "you"}  # fmt: skip
# a line this long and at least this Polish is a Polish sentence (e.g. a Polish news digest):
# read whole by the Polish voice instead of switching voices word by word
SENTENCE_WORDS, SENTENCE_SHARE = 6, 0.5


def is_polish(word: str, lexicon: set[str]) -> bool:
    w = word.lower()
    if not w.isalpha() or w in ENGLISH:
        return False
    return (
        w in POLISH_WORDS
        or bool(POLISH_LETTERS & set(w))
        or fold(w) in lexicon
        or bool(POLISH_SPELLING.search(w))
    )


def is_polish_sentence(text: str, lexicon: set[str]) -> bool:
    """Long, mostly Polish, and no English function words (our lines with stop names have them:
    "OSOBOWICE from Kościuszki at 20:47")."""
    words = [w for w in re.findall(r"\w+", text) if w.isalpha()]
    if len(words) < SENTENCE_WORDS or any(w.lower() in ENGLISH_GLUE for w in words):
        return False
    return sum(is_polish(w, lexicon) for w in words) >= SENTENCE_SHARE * len(words)


NEVER_IN_NAMES = ENGLISH | ENGLISH_GLUE | {"am", "pm"}


def _polish_words(parts: list[str], lexicon: set[str]) -> dict[int, bool]:
    """is_polish() for each word (the odd parts), then whole names: a capitalised word (or a
    lowercase "i") next to a Polish word, only a space between, is part of the same name:
    "Studium Wychowania Fizycznego i Sportu" is all Polish, not just "Fizycznego"."""
    polish = {i: is_polish(parts[i], lexicon) for i in range(1, len(parts), 2)}

    def can_join(w: str) -> bool:
        return w.isalpha() and (w[0].isupper() or w == "i") and w.lower() not in NEVER_IN_NAMES

    changed = True
    while changed:
        changed = False
        for i in polish:
            if (
                not polish[i]
                and can_join(parts[i])
                and (
                    (polish.get(i - 2) and parts[i - 1].isspace())
                    or (polish.get(i + 2) and parts[i + 1].isspace())
                )
            ):
                polish[i] = changed = True
    return polish


def split_languages(text: str, lexicon: set[str]) -> list[tuple[str, bool]]:
    """Text → [(chunk, is_polish)], consecutive words of one language kept together.
    Spaces and punctuation stay with the chunk they follow."""
    # "Mateusz's birthday" → "Mateusz birthday": a Polish voice can't do English possessives
    text = re.sub(r"(\w+)['’]s\b", lambda m: m[1] if is_polish(m[1], lexicon) else m[0], text)
    parts = re.split(r"(\w+)", text)
    words = _polish_words(parts, lexicon)
    chunks: list[list] = []
    for i, part in enumerate(parts):
        if not part:
            continue
        polish = words[i] if i % 2 else None  # odd parts are words
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


def synthesize_line(text: str, voice: str, polish_voice: str, lexicon: set[str]) -> bytes:
    """A line in one language (a headline, an event or job title): read whole by the voice of
    most of its words, never switching mid-line like synthesize_mixed()."""
    words = [w for w in re.findall(r"\w+", text) if w.isalpha()]
    if words and sum(is_polish(w, lexicon) for w in words) > len(words) / 2:
        return synthesize(_for_polish_voice(polish_times(text)), polish_voice)
    return synthesize(english_times(text), voice)


def synthesize_mixed(text: str, voice: str, polish_voice: str, lexicon: set[str]) -> bytes:
    """Like synthesize(), with the Polish words read by `polish_voice` (a whole Polish sentence
    by it alone)."""
    if is_polish_sentence(text, lexicon):
        return synthesize(_for_polish_voice(polish_times(text)), polish_voice)
    text = english_times(text)  # times are digits: always in the English voice's chunks
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
