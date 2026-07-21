"""Translation: CS2 glossary → DeepL (optional) → Google fallback."""

from __future__ import annotations

import os

from languages import Lang, get_lang
from memory_db import LearningDB

# DeepL free/pro endpoint language codes (subset we support)
_DEEPL_TARGETS = {
    "de": "DE",
    "en": "EN-US",
    "ru": "RU",
    "es": "ES",
    "pl": "PL",
    "fr": "FR",
    "ja": "JA",
    "zh": "ZH",
    # hi not supported by DeepL → Google only
}

_DEEPL_SOURCES = {
    "de": "DE",
    "en": "EN",
    "ru": "RU",
    "es": "ES",
    "pl": "PL",
    "fr": "FR",
    "ja": "JA",
    "zh": "ZH",
}


def _deepl_api_key() -> str:
    """Prefer env, then settings.json."""
    key = (os.environ.get("DEEPL_API_KEY") or os.environ.get("DEEPL_AUTH_KEY") or "").strip()
    if key:
        return key
    try:
        from config import load_settings

        v = load_settings().get("deepl_api_key")
        return str(v).strip() if v else ""
    except Exception:  # noqa: BLE001
        return ""


class Translator:
    def __init__(self, db: LearningDB | None = None) -> None:
        self.db = db or LearningDB()
        self._engine: str = "google"
        self._deepl_key = _deepl_api_key()
        if self._deepl_key:
            self._engine = "deepl"
            print("Translation engine: DeepL (API key found)", flush=True)
        else:
            print(
                "Translation engine: Google (optional: set DEEPL_API_KEY for better DE quality)",
                flush=True,
            )

    @property
    def engine(self) -> str:
        return self._engine

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

        online = self._online(text, lang, src_short)
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

    def _online(self, text: str, lang: Lang, source: str) -> str:
        # 1) DeepL when key present and language supported
        if self._deepl_key and lang.code in _DEEPL_TARGETS:
            out = self._deepl(text, lang.code, source)
            if out:
                self._engine = "deepl"
                return out
        # 2) Google (deep-translator, no key)
        out = self._google(text, lang.google, source)
        self._engine = "google"
        return out

    def _deepl(self, text: str, target_code: str, source: str) -> str:
        """DeepL Free or Pro REST API."""
        target = _DEEPL_TARGETS.get(target_code)
        if not target:
            return ""
        src_param = None
        if source and source != "auto":
            src_param = _DEEPL_SOURCES.get(source.split("-")[0].lower())

        # Free keys end with :fx → api-free.deepl.com; Pro → api.deepl.com
        key = self._deepl_key
        host = (
            "https://api-free.deepl.com"
            if key.endswith(":fx")
            else "https://api.deepl.com"
        )

        try:
            import urllib.error
            import urllib.parse
            import urllib.request
            import json

            data = {"text": text, "target_lang": target}
            if src_param:
                data["source_lang"] = src_param
            # DeepL form-style multi text
            body = urllib.parse.urlencode(data, doseq=True).encode("utf-8")
            req = urllib.request.Request(
                f"{host}/v2/translate",
                data=body,
                method="POST",
                headers={
                    "Authorization": f"DeepL-Auth-Key {key}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            translations = payload.get("translations") or []
            if not translations:
                return ""
            return str(translations[0].get("text") or "").strip()
        except Exception as exc:  # noqa: BLE001
            print(f"DeepL fallback → Google ({exc})", flush=True)
            return ""

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
