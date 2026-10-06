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
    assert split("21:00 Spotkanie TALON") == [("21:00", "en"), ("Spotkanie", "pl"), ("TALON", "en")]
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
