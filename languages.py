"""Supported output languages for translation display."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Lang:
    code: str
    label: str
    name_de: str
    google: str


OUTPUT_LANGUAGES: list[Lang] = [
    Lang("de", "DE", "Deutsch", "de"),
    Lang("en", "EN", "Englisch", "en"),
    Lang("ru", "RU", "Russisch", "ru"),
    Lang("es", "ES", "Spanisch", "es"),
    Lang("pl", "PL", "Polnisch", "pl"),
    Lang("fr", "FR", "Französisch", "fr"),
    Lang("zh", "ZH", "Chinesisch", "zh-CN"),
    Lang("ja", "JA", "Japanisch", "ja"),
    Lang("hi", "HI", "Hindi (Indisch)", "hi"),
]

_BY_CODE = {lang.code: lang for lang in OUTPUT_LANGUAGES}


def get_lang(code: str) -> Lang:
    code = (code or "de").lower().strip()
    if code in ("cn", "zh-cn", "zh_cn"):
        code = "zh"
    if code in ("in", "ind", "hindi", "indian"):
        code = "hi"
    if code in ("pl", "pl-pl", "polish", "polnisch", "polski"):
        code = "pl"
    return _BY_CODE.get(code, _BY_CODE["de"])


def cycle_lang(code: str, step: int = 1) -> Lang:
    codes = [lang.code for lang in OUTPUT_LANGUAGES]
    try:
        i = codes.index(get_lang(code).code)
    except ValueError:
        i = 0
    return OUTPUT_LANGUAGES[(i + step) % len(codes)]


def lang_help_text() -> str:
    return ", ".join(f"{lang.label}={lang.name_de}" for lang in OUTPUT_LANGUAGES)
