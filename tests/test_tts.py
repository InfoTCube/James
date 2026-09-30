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
