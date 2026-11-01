from assistant.core.tts import _for_polish_voice, split_languages

LEXICON = {"dworzec", "glowny", "rynek", "most", "grunwaldzki", "osobowice", "plac"}


def split(text: str) -> list[tuple[str, str]]:
    return [(c.strip(), "pl" if p else "en") for c, p in split_languages(text, LEXICON)]


def test_polish_names_go_to_the_polish_voice():
    assert split("🚋 16 → OSOBOWICE from Kościuszki at 20:47 (arrives most Grunwaldzki 20:53)") == [
        ("🚋 16 →", "en"),
        ("OSOBOWICE", "pl"),  # stop name (timetable lexicon)
        ("from", "en"),
        ("Kościuszki", "pl"),  # Polish letters
        ("at 20:47 (arrives", "en"),
        ("most Grunwaldzki", "pl"),
        ("20:53)", "en"),
    ]
    assert split("21:00 Spotkanie TALON") == [("21:00", "en"), ("Spotkanie TALON", "pl")]


def test_names_go_to_one_voice_whole():
    assert split("Next: Siatkówka at Studium Wychowania Fizycznego i Sportu, Wrocław, Poland.") == [
        ("Next:", "en"),
        ("Siatkówka", "pl"),
        ("at", "en"),
        ("Studium Wychowania Fizycznego i Sportu, Wrocław,", "pl"),
        ("Poland.", "en"),
    ]
    assert split("(arrives Chełmońskiego 17:02)") == [  # English words don't join a name
        ("(arrives", "en"),
        ("Chełmońskiego", "pl"),
        ("17:02)", "en"),
    ]
    assert split("Light jacket, rain tomorrow") == [("Light jacket, rain tomorrow", "en")]


def test_possessive_and_abbreviations():
    assert split("Mateusz's birthday tomorrow") == [("Mateusz", "pl"), ("birthday tomorrow", "en")]
    assert split("It's 07:00") == [("It's 07:00", "en")]
    assert _for_polish_voice("PL. GRUNWALDZKI ") == "plac grunwaldzki "


def test_polish_sentences_are_read_whole_by_the_polish_voice():
    from assistant.core.tts import is_polish_sentence

    polish = [
        "Pięć ofert w przetargu na autobusy w gminie Wieluń.",
        "Rosja grozi sąsiadowi Polski, mówi o kolejnej eskalacji napięć.",
        "W Wieluniu powstanie nowy supermarket przy ulicy Wojska Polskiego.",
    ]
    english = [
        "Mateusz's birthday tomorrow",  # short: names only, voices switch per word
        "🚋 16 → OSOBOWICE from Kościuszki at 20:47 (arrives most Grunwaldzki 20:53)",
        "Leave by 08:15 for Wroc.NET meetup at Wędrówki Pub on Podwale",
        "Light jacket, rain tomorrow, 12 degrees in the afternoon",
    ]
    assert [is_polish_sentence(t, LEXICON) for t in polish] == [True] * 3
    assert [is_polish_sentence(t, LEXICON) for t in english] == [False] * 4


def test_headlines_are_read_in_one_language(monkeypatch):
    from assistant.core import tts

    voices = []
    monkeypatch.setattr(tts, "synthesize", lambda text, voice: voices.append(voice) or b"")
    for line in ["Pożar na Krzykach, strażacy w akcji", "Nowy most otwarty",
                 "Poland wins the vote in Brussels", "Junior .NET Developer, Wrocław"]:  # fmt: skip
        tts.synthesize_line(line, "en", "pl", LEXICON)
    assert voices == ["pl", "pl", "en", "en"]


def test_times_read_as_times():
    from assistant.core.tts import english_times, polish_times

    assert english_times("08:00–16:00  Praca") == "from 8 AM to 4 PM  Praca"
    assert english_times("Leave by 07:40 for uni (12:00)") == "Leave by 7:40 AM for uni (12 PM)"
    assert english_times("Wed 07.10 at 00:30") == "Wednesday 7 October at 12:30 AM"
    assert english_times("14.10 or 12.50 zł") == "14 October or 12.50 zł"  # not a date: stays
    assert polish_times("Mecz o 20:45, otwarcie 08:00") == "Mecz o 20 45, otwarcie 8"
    assert split("from 8 AM to 4 PM Praca") == [("from 8 AM to 4 PM", "en"), ("Praca", "pl")]
