"""Translation with learning DB + Google fallback."""

from __future__ import annotations

from languages import Lang, get_lang
from memory_db import LearningDB


class Translator:
    def __init__(self, db: LearningDB | None = None) -> None:
        self.db = db or LearningDB()

    def translate(
        self,
        text: str,
        target: str | Lang,
        source: str = "auto",
        *,
        learn: bool = True,
    ) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        lang = get_lang(target.code if isinstance(target, Lang) else target)
        src = source if source and source != "auto" else "auto"
        src_short = src.split("-")[0].lower() if src != "auto" else "auto"
        if src_short != "auto" and src_short == lang.code:
            return text

        hit = self.db.lookup(text, lang.code, src_short)
        if hit is not None:
            if learn:
                self.db.learn(text, hit.translation, lang.code, src_short, bump=1)
            return hit.translation

        online = self._google(text, lang.google, src_short)
        out = self.db.apply_token_glossary(online, lang.code)
        if learn and out:
            self.db.learn(text, out, lang.code, src_short, bump=1)
        return out

    def pin(
        self,
        source: str,
        translation: str,
        target: str | Lang,
        source_lang: str = "auto",
    ) -> None:
        lang = get_lang(target.code if isinstance(target, Lang) else target)
        self.db.pin_pair(source, translation, lang.code, source_lang)

    def _google(self, text: str, target_google: str, source: str) -> str:
        try:
            from deep_translator import GoogleTranslator

            src_map = {"zh": "zh-CN", "auto": "auto"}
            src_code = src_map.get(source, source)
            if src_code != "auto" and len(src_code) > 5:
                src_code = "auto"
            out = GoogleTranslator(source=src_code, target=target_google).translate(text)
            return (out or text).strip()
        except Exception:  # noqa: BLE001
            return text
