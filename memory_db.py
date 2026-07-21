"""Persistent learning database (SQLite) — glossary + history."""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass


def _norm(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[\"'`]+", "", t)
    return t


from cs2_callouts import CS2_GLOSSARY_SEED

_SEED_GLOSSARY: list[tuple[str, str, str, str]] = list(CS2_GLOSSARY_SEED)


@dataclass
class GlossaryHit:
    translation: str
    hits: int
    pinned: bool
    source: str


class LearningDB:
    def __init__(self, path: str | None = None) -> None:
        if path:
            self.path = path
        else:
            try:
                from paths import data_path

                self.path = data_path("learning.db")
            except Exception:  # noqa: BLE001
                base = os.path.dirname(os.path.abspath(__file__))
                self.path = os.path.join(base, "learning.db")
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        self._seed_if_empty()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS glossary (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_norm TEXT NOT NULL,
                    source_raw TEXT NOT NULL,
                    source_lang TEXT NOT NULL DEFAULT 'auto',
                    target_lang TEXT NOT NULL,
                    translation TEXT NOT NULL,
                    hits INTEGER NOT NULL DEFAULT 1,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_glossary_key
                    ON glossary(source_norm, source_lang, target_lang);
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_raw TEXT NOT NULL,
                    source_lang TEXT,
                    target_lang TEXT NOT NULL,
                    translation TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                """
            )
            self._conn.commit()

    def _seed_if_empty(self) -> None:
        with self._lock:
            n = self._conn.execute("SELECT COUNT(*) AS c FROM glossary").fetchone()["c"]
            if n > 0:
                return
            now = time.time()
            self._conn.executemany(
                """
                INSERT OR IGNORE INTO glossary
                (source_norm, source_raw, source_lang, target_lang, translation, hits, pinned, updated_at)
                VALUES (?, ?, ?, ?, ?, 3, 1, ?)
                """,
                [
                    (_norm(raw), raw, src_l, tgt_l, tr, now)
                    for raw, src_l, tgt_l, tr in _SEED_GLOSSARY
                ],
            )
            self._conn.commit()

    def stats(self) -> dict[str, int]:
        with self._lock:
            g = self._conn.execute("SELECT COUNT(*) AS c FROM glossary").fetchone()["c"]
            h = self._conn.execute("SELECT COUNT(*) AS c FROM history").fetchone()["c"]
            p = self._conn.execute(
                "SELECT COUNT(*) AS c FROM glossary WHERE pinned=1"
            ).fetchone()["c"]
        return {"glossary": int(g), "history": int(h), "pinned": int(p)}

    def lookup(
        self, text: str, target_lang: str, source_lang: str = "auto"
    ) -> GlossaryHit | None:
        sn = _norm(text)
        if not sn:
            return None
        src = (source_lang or "auto").split("-")[0].lower()
        with self._lock:
            row = self._conn.execute(
                """
                SELECT translation, hits, pinned FROM glossary
                WHERE source_norm=? AND target_lang=?
                  AND source_lang IN (?, 'auto', 'en', ?)
                ORDER BY pinned DESC, hits DESC LIMIT 1
                """,
                (sn, target_lang, src, src),
            ).fetchone()
        if not row:
            return None
        return GlossaryHit(
            translation=row["translation"],
            hits=int(row["hits"]),
            pinned=bool(row["pinned"]),
            source="exact",
        )

    def apply_token_glossary(self, text: str, target_lang: str) -> str:
        if not text.strip():
            return text
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT source_norm, translation, length(source_norm) AS L
                FROM glossary WHERE target_lang=?
                ORDER BY L DESC LIMIT 400
                """,
                (target_lang,),
            ).fetchall()
        out = text
        for row in rows:
            needle = row["source_norm"]
            if len(needle) < 2:
                continue
            pattern = re.compile(rf"(?<!\w){re.escape(needle)}(?!\w)", re.IGNORECASE)
            if pattern.search(out):
                out = pattern.sub(row["translation"], out)
        return out

    def learn(
        self,
        source_raw: str,
        translation: str,
        target_lang: str,
        source_lang: str = "auto",
        *,
        pinned: bool = False,
        bump: int = 1,
    ) -> None:
        source_raw = (source_raw or "").strip()
        translation = (translation or "").strip()
        if not source_raw or not translation:
            return
        sn = _norm(source_raw)
        src = (source_lang or "auto").split("-")[0].lower()
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, hits, pinned FROM glossary
                WHERE source_norm=? AND source_lang=? AND target_lang=?
                """,
                (sn, src, target_lang),
            ).fetchone()
            if row:
                new_pin = 1 if pinned or row["pinned"] else 0
                if row["pinned"] and not pinned:
                    self._conn.execute(
                        "UPDATE glossary SET hits=hits+?, updated_at=? WHERE id=?",
                        (bump, now, row["id"]),
                    )
                else:
                    self._conn.execute(
                        """
                        UPDATE glossary
                        SET translation=?, hits=hits+?, pinned=?, updated_at=?, source_raw=?
                        WHERE id=?
                        """,
                        (translation, bump, new_pin, now, source_raw, row["id"]),
                    )
            else:
                self._conn.execute(
                    """
                    INSERT INTO glossary
                    (source_norm, source_raw, source_lang, target_lang, translation, hits, pinned, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (sn, source_raw, src, target_lang, translation, max(1, bump), 1 if pinned else 0, now),
                )
            self._conn.execute(
                """
                INSERT INTO history (source_raw, source_lang, target_lang, translation, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (source_raw, src, target_lang, translation, now),
            )
            self._conn.execute(
                """
                DELETE FROM history WHERE id NOT IN (
                    SELECT id FROM history ORDER BY id DESC LIMIT 5000
                )
                """
            )
            self._conn.commit()

    def pin_pair(
        self,
        source_raw: str,
        translation: str,
        target_lang: str,
        source_lang: str = "auto",
    ) -> None:
        self.learn(source_raw, translation, target_lang, source_lang, pinned=True, bump=5)
