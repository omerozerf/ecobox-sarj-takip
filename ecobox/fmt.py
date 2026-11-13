"""Small Turkish formatting helpers shared by the reply texts and the findings."""

from datetime import timedelta

# Third-person possessive suffix after a number read aloud, chosen by its last number word:
# %45 is "yüzde kırk beş" -> "%45'i"; %40 "kırk" -> "%40'ı"; %100 "yüz" -> "%100'ü".
_POSSESSIVE_BY_UNIT = {1: "i", 2: "si", 3: "ü", 4: "ü", 5: "i", 6: "sı", 7: "si", 8: "i",
                       9: "u"}
_POSSESSIVE_BY_TENS = {1: "u", 2: "si", 3: "u", 4: "ı", 5: "si", 6: "ı", 7: "i", 8: "i",
                       9: "ı"}

# Locative suffix (-de/-da/-te/-ta) after a number read aloud, by its last number word:
# 25 is "yirmi beş" -> "25'te"; 30 "otuz" -> "30'da"; 0 "sıfır" -> "0'da".
_LOCATIVE_BY_UNIT = {1: "de", 2: "de", 3: "te", 4: "te", 5: "te", 6: "da", 7: "de", 8: "de",
                     9: "da"}
_LOCATIVE_BY_TENS = {0: "da", 1: "da", 2: "de", 3: "da", 4: "ta", 5: "de", 6: "ta", 7: "de",
                     8: "de", 9: "da"}
_FRONT_VOWELS = "eiöü"
_BACK_VOWELS = "aıou"
_VOICELESS = "çfhkpsşt"


def duration_text(value: timedelta) -> str:
    minutes = max(0, round(value.total_seconds() / 60))
    hours, minutes = divmod(minutes, 60)
    if hours and minutes:
        return f"{hours} sa {minutes} dk"
    return f"{hours} sa" if hours else f"{minutes} dk"


def minutes_text(value: float) -> str:
    return duration_text(timedelta(minutes=value))


def decimal(value: float, digits: int = 1) -> str:
    """Turkish decimal comma, trailing ",0" dropped: 2.34 -> "2,3", 3.0 -> "3"."""
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def possessive(number: int) -> str:
    """Apostrophe and possessive suffix for a whole number: 45 -> "'i", 40 -> "'ı"."""
    if number == 0:
        return "'ı"
    if number % 1000 == 0:
        return "'i"  # bin
    if number % 100 == 0:
        return "'ü"  # yüz
    if number % 10:
        return "'" + _POSSESSIVE_BY_UNIT[number % 10]
    return "'" + _POSSESSIVE_BY_TENS[(number % 100) // 10]


def half_up(value: float) -> int:
    """Rounds .5 up (Python's round() goes to even: round(2.5) == 2)."""
    return int(value + 0.5) if value >= 0 else -int(-value + 0.5)


def percent(share: float) -> str:
    return f"%{half_up(100 * share)}"


def percent_of(share: float) -> str:
    """"%45'i": a percentage used as "the N percent of ..."."""
    value = half_up(100 * share)
    return f"%{value}{possessive(value)}"


def hour_range(hour: int, span: int = 1) -> str:
    return f"{hour:02d}:00–{(hour + span) % 24:02d}:00"


def span_text(days: float) -> str:
    """How long the data covers: minutes below an hour, hours below a day, then days."""
    if days * 24 < 1:
        return f"{max(1, half_up(days * 1440))} dakika"
    if days < 1:
        return f"{half_up(days * 24)} saat"
    return f"{decimal(days)} gün"


def span_adjective(days: float) -> str:
    """"12 dakikalık", "6 saatlik", "2,3 günlük": the span as an adjective."""
    text = span_text(days)
    if text.endswith("dakika"):
        return text + "lık"
    return text + ("lik" if text.endswith("saat") else "lük")


def capitalised(text: str) -> str:
    first = "İ" if text[0] == "i" else text[0].upper()
    return first + text[1:]


def bar(share: float | None, width: int = 10) -> str:
    """A text bar for a share in [0, 1]; no data is a dotted bar, visibly not "0 %"."""
    if share is None:
        return "·" * width
    filled = half_up(share * width)
    return "█" * filled + "░" * (width - filled)


def number_locative(number: int) -> str:
    """"'te"/"'de"/"'da"/"'ta" after a whole number: 25 -> "'te", 30 -> "'da"."""
    if number % 100 == 0 and number:
        return "'de"  # yüz, bin
    unit = number % 10
    return "'" + (_LOCATIVE_BY_UNIT[unit] if unit else _LOCATIVE_BY_TENS[(number % 100) // 10])


def locative(name: str) -> str:
    """A place name with its locative suffix: "Ego'da", "Hacettepe'de", "F blok'ta".

    Follows the last vowel (vowel harmony) and the last letter (consonant hardening). A last
    word without vowels is read letter by letter ("F" is "fe", "DC" is "de ce"): "'de". A
    trailing number takes the suffix of its last spoken word ("Ego 2'de").
    """
    word = name.rstrip().split()[-1]
    if word.isdigit():
        return name.rstrip() + number_locative(int(word))
    lower = word.replace("I", "ı").replace("İ", "i").lower()
    vowels = [c for c in lower if c in _FRONT_VOWELS + _BACK_VOWELS]
    if not vowels:
        return name.rstrip() + "'de"
    vowel = "e" if vowels[-1] in _FRONT_VOWELS else "a"
    consonant = "t" if lower[-1] in _VOICELESS else "d"
    return f"{name.rstrip()}'{consonant}{vowel}"


def percent_in(share: float) -> str:
    """"%58'inde": a percentage as "in N percent of ..." (possessive plus locative)."""
    text = percent_of(share)
    return text + ("nde" if text[-1] in "iü" else "nda")


def percent_from(share: float) -> str:
    """"%10'undan": a percentage as "than N percent" (possessive plus ablative)."""
    text = percent_of(share)
    return text + ("nden" if text[-1] in "iü" else "ndan")


def clock_range(start: int, end: int) -> str:
    """"11–13": a span of whole local hours, for chart titles."""
    return f"{start:02d}–{end % 24:02d}"
