"""Languages understood by Whisper (speech) and offered for translation.

``LANGUAGES`` keeps Whisper's order of prevalence; the first entries are shown first in the UI.
Names are given in French and English; the flag is the most common country for the language.
"""
from __future__ import annotations

# code, French name, English name, flag
_TABLE: list[tuple[str, str, str, str]] = [
    ("fr", "Français", "French", "🇫🇷"), ("en", "Anglais", "English", "🇬🇧"), ("es", "Espagnol", "Spanish", "🇪🇸"),
    ("it", "Italien", "Italian", "🇮🇹"), ("de", "Allemand", "German", "🇩🇪"), ("pt", "Portugais", "Portuguese", "🇵🇹"),
    ("ja", "Japonais", "Japanese", "🇯🇵"), ("ko", "Coréen", "Korean", "🇰🇷"), ("zh", "Chinois", "Chinese", "🇨🇳"),
    ("ar", "Arabe", "Arabic", "🇸🇦"), ("ru", "Russe", "Russian", "🇷🇺"), ("nl", "Néerlandais", "Dutch", "🇳🇱"),
    ("tr", "Turc", "Turkish", "🇹🇷"), ("pl", "Polonais", "Polish", "🇵🇱"), ("uk", "Ukrainien", "Ukrainian", "🇺🇦"),
    ("hi", "Hindi", "Hindi", "🇮🇳"), ("sv", "Suédois", "Swedish", "🇸🇪"), ("da", "Danois", "Danish", "🇩🇰"),
    ("no", "Norvégien", "Norwegian", "🇳🇴"), ("fi", "Finnois", "Finnish", "🇫🇮"), ("el", "Grec", "Greek", "🇬🇷"),
    ("he", "Hébreu", "Hebrew", "🇮🇱"), ("cs", "Tchèque", "Czech", "🇨🇿"), ("ro", "Roumain", "Romanian", "🇷🇴"),
    ("hu", "Hongrois", "Hungarian", "🇭🇺"), ("vi", "Vietnamien", "Vietnamese", "🇻🇳"), ("th", "Thaï", "Thai", "🇹🇭"),
    ("id", "Indonésien", "Indonesian", "🇮🇩"), ("ms", "Malais", "Malay", "🇲🇾"), ("fa", "Persan", "Persian", "🇮🇷"),
    ("ca", "Catalan", "Catalan", "🏴"), ("bg", "Bulgare", "Bulgarian", "🇧🇬"), ("hr", "Croate", "Croatian", "🇭🇷"),
    ("sr", "Serbe", "Serbian", "🇷🇸"), ("sk", "Slovaque", "Slovak", "🇸🇰"), ("sl", "Slovène", "Slovenian", "🇸🇮"),
    ("lt", "Lituanien", "Lithuanian", "🇱🇹"), ("lv", "Letton", "Latvian", "🇱🇻"), ("et", "Estonien", "Estonian", "🇪🇪"),
    ("ta", "Tamoul", "Tamil", "🇮🇳"), ("ur", "Ourdou", "Urdu", "🇵🇰"), ("bn", "Bengali", "Bengali", "🇧🇩"),
    ("tl", "Tagalog", "Tagalog", "🇵🇭"), ("sw", "Swahili", "Swahili", "🇰🇪"), ("af", "Afrikaans", "Afrikaans", "🇿🇦"),
    ("gl", "Galicien", "Galician", "🇪🇸"), ("eu", "Basque", "Basque", "🇪🇸"), ("cy", "Gallois", "Welsh", "🏴"),
    ("is", "Islandais", "Icelandic", "🇮🇸"), ("mk", "Macédonien", "Macedonian", "🇲🇰"), ("bs", "Bosnien", "Bosnian", "🇧🇦"),
    ("sq", "Albanais", "Albanian", "🇦🇱"), ("hy", "Arménien", "Armenian", "🇦🇲"), ("ka", "Géorgien", "Georgian", "🇬🇪"),
    ("az", "Azéri", "Azerbaijani", "🇦🇿"), ("kk", "Kazakh", "Kazakh", "🇰🇿"), ("uz", "Ouzbek", "Uzbek", "🇺🇿"),
    ("be", "Biélorusse", "Belarusian", "🇧🇾"), ("mn", "Mongol", "Mongolian", "🇲🇳"), ("ne", "Népalais", "Nepali", "🇳🇵"),
    ("mr", "Marathi", "Marathi", "🇮🇳"), ("te", "Télougou", "Telugu", "🇮🇳"), ("kn", "Kannada", "Kannada", "🇮🇳"),
    ("ml", "Malayalam", "Malayalam", "🇮🇳"), ("pa", "Pendjabi", "Punjabi", "🇮🇳"), ("gu", "Gujarati", "Gujarati", "🇮🇳"),
    ("si", "Cingalais", "Sinhala", "🇱🇰"), ("km", "Khmer", "Khmer", "🇰🇭"), ("lo", "Lao", "Lao", "🇱🇦"),
    ("my", "Birman", "Burmese", "🇲🇲"), ("am", "Amharique", "Amharic", "🇪🇹"), ("so", "Somali", "Somali", "🇸🇴"),
    ("yo", "Yoruba", "Yoruba", "🇳🇬"), ("ha", "Haoussa", "Hausa", "🇳🇬"), ("ln", "Lingala", "Lingala", "🇨🇩"),
    ("mg", "Malgache", "Malagasy", "🇲🇬"), ("ht", "Créole haïtien", "Haitian Creole", "🇭🇹"),
    ("lb", "Luxembourgeois", "Luxembourgish", "🇱🇺"), ("mt", "Maltais", "Maltese", "🇲🇹"), ("la", "Latin", "Latin", "🇻🇦"),
    ("yue", "Cantonais", "Cantonese", "🇭🇰"), ("haw", "Hawaïen", "Hawaiian", "🇺🇸"), ("jw", "Javanais", "Javanese", "🇮🇩"),
    ("su", "Soundanais", "Sundanese", "🇮🇩"), ("ps", "Pachto", "Pashto", "🇦🇫"), ("tg", "Tadjik", "Tajik", "🇹🇯"),
    ("tk", "Turkmène", "Turkmen", "🇹🇲"), ("tt", "Tatar", "Tatar", "🇷🇺"), ("ba", "Bachkir", "Bashkir", "🇷🇺"),
    ("oc", "Occitan", "Occitan", "🇫🇷"), ("br", "Breton", "Breton", "🇫🇷"), ("fo", "Féroïen", "Faroese", "🇫🇴"),
    ("nn", "Nynorsk", "Nynorsk", "🇳🇴"), ("sa", "Sanskrit", "Sanskrit", "🇮🇳"), ("sd", "Sindhi", "Sindhi", "🇵🇰"),
    ("as", "Assamais", "Assamese", "🇮🇳"), ("bo", "Tibétain", "Tibetan", "🇨🇳"), ("sn", "Shona", "Shona", "🇿🇼"),
    ("mi", "Maori", "Maori", "🇳🇿"), ("yi", "Yiddish", "Yiddish", "🏳️"),
]

LANGUAGES: dict[str, dict[str, str]] = {c: {"code": c, "fr": fr, "en": en, "flag": flag} for c, fr, en, flag in _TABLE}
FEATURED = ["fr", "en", "es", "it", "de", "pt", "ja", "ko", "zh", "ar", "ru", "nl", "tr", "pl"]
# Scripts written without spaces between words: line breaking and reading speed work per character
NO_SPACES = {"ja", "zh", "yue", "th", "lo", "km", "my", "bo"}
RTL = {"ar", "he", "fa", "ur", "ps", "yi", "sd"}


def name(code: str, french: bool = True) -> str:
    lang = LANGUAGES.get(code)
    return (lang["fr"] if french else lang["en"]) if lang else code


def english_name(code: str) -> str:
    return LANGUAGES.get(code, {}).get("en", code)


def catalog() -> list[dict]:
    featured = [LANGUAGES[c] for c in FEATURED]
    rest = sorted((v for k, v in LANGUAGES.items() if k not in FEATURED), key=lambda v: v["fr"])
    return [{**v, "featured": v["code"] in FEATURED} for v in featured + rest]
