import re


# ==========================================================
# SCRIPT DETECTION
# ==========================================================

def contains_devanagari(text):
    """
    Hindi and Marathi commonly use Devanagari.
    Unicode block:
    U+0900 - U+097F
    """

    return bool(
        re.search(
            r"[\u0900-\u097F]",
            text
        )
    )


def contains_latin(text):
    """
    Detect English / Roman-script characters.
    """

    return bool(
        re.search(
            r"[A-Za-z]",
            text
        )
    )


# ==========================================================
# SEGMENT LANGUAGE STYLE
# ==========================================================

def classify_script_usage(text):
    """
    NOTE:
    This is NOT claiming perfect linguistic
    classification.

    It tells us what writing systems appear
    in Whisper's transcription.
    """

    devanagari = contains_devanagari(text)
    latin = contains_latin(text)

    if devanagari and latin:
        return "mixed_devanagari_latin"

    if devanagari:
        return "devanagari"

    if latin:
        return "latin"

    return "unknown"


# ==========================================================
# CODE SWITCH FLAG
# ==========================================================

def detect_possible_code_switch(
    text,
    chunk_language
):
    """
    Conservative indicator.

    Example:
    Hindi chunk + English terms
    or mixed scripts.
    """

    script = classify_script_usage(text)

    if script == "mixed_devanagari_latin":
        return True

    # Whisper may output Hinglish entirely
    # in Roman script, which cannot reliably
    # be solved using Unicode alone.
    #
    # We deliberately avoid pretending
    # that Roman Hindi == English here.

    return False


# ==========================================================
# LANGUAGE NAME
# ==========================================================

def language_name(code):

    names = {
        "en": "English",
        "hi": "Hindi",
        "mr": "Marathi",
        # Other codes Whisper reports, e.g. when auto-detection mislabels
        # accented English (seen: "ml" for English speech).
        "ml": "Malayalam",
        "ta": "Tamil",
        "te": "Telugu",
        "kn": "Kannada",
        "bn": "Bengali",
        "gu": "Gujarati",
        "pa": "Punjabi",
        "ur": "Urdu",
        "ne": "Nepali",
        "es": "Spanish",
        "fr": "French",
        "de": "German",
    }

    return names.get(
        code,
        "Other / Unknown"
    )