"""
CS2-only vocabulary: Whisper prompt bias + glossary seeds.

Not map-hacks — only text phrases for recognition/translation.
"""

from __future__ import annotations

# Whisper initial_prompt (English backbone; team languages mixed in chat)
CS2_WHISPER_PROMPT = (
    "Counter-Strike 2 live team voice chat only. Short radio callouts. "
    "Words: smoke flash molotov HE nade decoy. "
    "Sites: A B mid CT T spawn. "
    "Maps: Mirage Inferno Dust2 Ancient Anubis Nuke Overpass Vertigo. "
    "Mirage: apps palace under connector jungle stairs window top mid bottom mid cat. "
    "Inferno: banana apartments arch pit coffin library. "
    "Dust2: long short cat tunnels upper lower. "
    "Actions: rotate stack rush default execute retake save eco full buy force buy drop. "
    "Info: one HP low one shot trade peek don't peek AWP. "
    "Languages: English German Russian Polish Spanish French. "
    "Ignore gunshots footsteps music. Only human speech."
)

# (source_raw, source_lang, target_lang, preferred_translation)
# Multi-target for common EN callouts → DE/RU/PL etc. when user picks target.
CS2_GLOSSARY_SEED: list[tuple[str, str, str, str]] = [
    # EN → DE
    ("eco", "en", "de", "Eco"),
    ("full buy", "en", "de", "Fullbuy"),
    ("force buy", "en", "de", "Force-Buy"),
    ("force", "en", "de", "Force"),
    ("save", "en", "de", "saven"),
    ("drop me", "en", "de", "drop mir"),
    ("drop", "en", "de", "Drop"),
    ("flash", "en", "de", "Flash"),
    ("flash bang", "en", "de", "Flashbang"),
    ("smoke mid", "en", "de", "Smoke Mid"),
    ("smoke A", "en", "de", "Smoke A"),
    ("smoke B", "en", "de", "Smoke B"),
    ("molly", "en", "de", "Molly"),
    ("molotov", "en", "de", "Molotov"),
    ("HE", "en", "de", "HE"),
    ("AWP mid", "en", "de", "AWP Mid"),
    ("he's low", "en", "de", "er ist low"),
    ("one HP", "en", "de", "ein HP"),
    ("one shot", "en", "de", "One-Shot"),
    ("rotate", "en", "de", "rotieren"),
    ("rotate A", "en", "de", "rotier A"),
    ("rotate B", "en", "de", "rotier B"),
    ("stack A", "en", "de", "A stacken"),
    ("stack B", "en", "de", "B stacken"),
    ("default", "en", "de", "Default"),
    ("rush B", "en", "de", "Rush B"),
    ("rush A", "en", "de", "Rush A"),
    ("don't peek", "en", "de", "nicht peeken"),
    ("nice trade", "en", "de", "schöner Trade"),
    ("play time", "en", "de", "Zeit spielen"),
    ("apps", "en", "de", "Apps"),
    ("palace", "en", "de", "Palace"),
    ("connector", "en", "de", "Connector"),
    ("jungle", "en", "de", "Jungle"),
    ("banana", "en", "de", "Banana"),
    ("long", "en", "de", "Long"),
    ("short", "en", "de", "Short"),
    ("cat", "en", "de", "Cat"),
    ("mid", "en", "de", "Mid"),
    ("top mid", "en", "de", "Top Mid"),
    ("bottom mid", "en", "de", "Bottom Mid"),
    ("under", "en", "de", "Under"),
    ("window", "en", "de", "Window"),
    ("tunnels", "en", "de", "Tunnels"),
    ("CT", "en", "de", "CT"),
    ("T side", "en", "de", "T-Seite"),
    ("A site", "en", "de", "A-Site"),
    ("B site", "en", "de", "B-Site"),
    ("bomb down", "en", "de", "Bombe liegt"),
    ("bomb plant", "en", "de", "Bombe gelegt"),
    ("defuse", "en", "de", "entschärfen"),
    ("check", "en", "de", "checken"),
    ("clear", "en", "de", "clear"),
    ("contact", "en", "de", "Kontakt"),
    ("last", "en", "de", "Letzter"),
    ("one left", "en", "de", "einer übrig"),
    ("two left", "en", "de", "zwei übrig"),
    ("three left", "en", "de", "drei übrig"),
    ("I'm dead", "en", "de", "ich bin tot"),
    ("need drop", "en", "de", "brauche Drop"),
    ("nice", "en", "de", "nice"),
    ("good job", "en", "de", "guter Job"),
    # EN → EN keep gaming terms (identity)
    ("eco", "en", "en", "eco"),
    ("flash", "en", "en", "flash"),
    ("smoke mid", "en", "en", "smoke mid"),
    # RU common → DE
    ("эй", "ru", "de", "hey"),
    ("иди", "ru", "de", "geh"),
    ("на а", "ru", "de", "auf A"),
    ("на б", "ru", "de", "auf B"),
    ("мид", "ru", "de", "Mid"),
    ("смок", "ru", "de", "Smoke"),
    ("флеш", "ru", "de", "Flash"),
    ("один", "ru", "de", "einer"),
    ("два", "ru", "de", "zwei"),
    ("три", "ru", "de", "drei"),
    ("я умер", "ru", "de", "ich bin tot"),
    ("сэйв", "ru", "de", "saven"),
    ("эко", "ru", "de", "Eco"),
    ("ротейт", "ru", "de", "rotieren"),
    ("бомба", "ru", "de", "Bombe"),
    # PL → DE
    ("mid", "pl", "de", "Mid"),
    ("flash", "pl", "de", "Flash"),
    ("smoke", "pl", "de", "Smoke"),
    ("eco", "pl", "de", "Eco"),
    ("rotuj", "pl", "de", "rotieren"),
    ("jeden", "pl", "de", "einer"),
    ("dwóch", "pl", "de", "zwei"),
]


def whisper_prompt() -> str:
    return CS2_WHISPER_PROMPT
