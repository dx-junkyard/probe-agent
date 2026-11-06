"""Run-independent Feature registry per System (IK-0038).

A feature key (``feature_drafts.feature_id``, and the ``feature_id`` that
experiments, probe plans and code links refer to) must not change when the
Feature Map is regenerated. Following the owner's rule (2026-11-07), the key
is minted and kept by the system; the drafts LLM never writes one. The prompt
shows the live registry as per-prompt handles ``F1..Fn`` and the model answers
each drafted feature with ``{"same_as": "F3"}`` or ``{"new": true}``; the
system maps handles back to keys and mints keys for ``new`` features.

Schema (``feature_registry``)::

    id            INTEGER PRIMARY KEY
    system_id     INTEGER NOT NULL
    feature_key   TEXT NOT NULL        -- UNIQUE (system_id, feature_key)
    display_name  TEXT NOT NULL        -- the name the key was minted from
    summary       TEXT NOT NULL        -- latest summary (shown to the model)
    origin        TEXT NOT NULL        -- 'minted' | 'migrated'
    created_at    REAL NOT NULL
    updated_at    REAL NOT NULL
    last_run_id   INTEGER              -- last drafts run that matched/minted it
    retired_at    REAL                 -- NULL = live (shown to the model)

Key minting (:func:`mint_feature_key`)::

    base = kebab(display_name)[:48]     # [a-z0-9] runs joined by "-"
    if base is empty (e.g. a Japanese name): base = "feature", always suffixed
    key  = base                          # when not taken
         = f"{base}-{sha256(display_name)[:4]}"   # on collision
         (then 6, 8, ... hex chars, then a counter)

"taken" covers every registered key (live or retired) and every feature_id a
past drafts run of the System ever used, so a new feature never inherits the
experiments or plans of an unrelated old id.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

_KEY_MAX = 48


@dataclass
class RegistryEntry:
    feature_key: str
    display_name: str
    summary: str = ""
    handle: str = ""
    evidence_paths: List[str] = field(default_factory=list)


def _kebab(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:_KEY_MAX].strip("-")


def name_slug(text: str) -> str:
    return _kebab(text)


def mint_feature_key(display_name: str, taken: Set[str]) -> str:
    """Mint a kebab key from the first display name; suffix only on collision."""
    base = _kebab(display_name)
    digest = hashlib.sha256((display_name or "").strip().encode("utf-8")).hexdigest()
    if base and base not in taken:
        return base
    stem = base or "feature"
    for length in (4, 6, 8, 12, 16):
        candidate = f"{stem}-{digest[:length]}"
        if candidate not in taken:
            return candidate
    n = 2
    while f"{stem}-{digest[:16]}-{n}" in taken:
        n += 1
    return f"{stem}-{digest[:16]}-{n}"


def _latest_completed_drafts_run(conn: sqlite3.Connection, system_id: int) -> Optional[int]:
    row = conn.execute(
        """SELECT id FROM intelligence_runs
           WHERE system_id = ? AND run_type = 'repository_drafts'
             AND status = 'completed'
           ORDER BY id DESC LIMIT 1""",
        (system_id,),
    ).fetchone()
    return row["id"] if row is not None else None


def ensure_seeded(conn: sqlite3.Connection, system_id: int) -> int:
    """Seed an empty registry from the latest completed drafts run.

    Existing feature ids keep working: each id of the latest run becomes a
    live registry key unchanged (``origin='migrated'``). Idempotent: does
    nothing once the System has any registry row. Returns rows inserted.
    """
    exists = conn.execute(
        "SELECT 1 FROM feature_registry WHERE system_id = ? LIMIT 1", (system_id,)
    ).fetchone()
    if exists is not None:
        return 0
    run_id = _latest_completed_drafts_run(conn, system_id)
    if run_id is None:
        return 0
    rows = conn.execute(
        """SELECT feature_id, name, summary, created_at FROM feature_drafts
           WHERE system_id = ? AND intelligence_run_id = ?
           ORDER BY id""",
        (system_id, run_id),
    ).fetchall()
    inserted = 0
    now = time.time()
    for row in rows:
        cur = conn.execute(
            """INSERT OR IGNORE INTO feature_registry
                   (system_id, feature_key, display_name, summary, origin,
                    created_at, updated_at, last_run_id)
               VALUES (?, ?, ?, ?, 'migrated', ?, ?, ?)""",
            (system_id, row["feature_id"], row["name"], row["summary"] or "",
             row["created_at"] or now, now, run_id),
        )
        inserted += cur.rowcount
    return inserted


def live_entries(conn: sqlite3.Connection, system_id: int) -> List[RegistryEntry]:
    """Live keys in registry order, with prompt handles F1..Fn and evidence."""
    rows = conn.execute(
        """SELECT feature_key, display_name, summary FROM feature_registry
           WHERE system_id = ? AND retired_at IS NULL
           ORDER BY id""",
        (system_id,),
    ).fetchall()
    entries = []
    for index, row in enumerate(rows, 1):
        paths: List[str] = []
        draft = conn.execute(
            """SELECT id FROM feature_drafts
               WHERE system_id = ? AND feature_id = ?
               ORDER BY id DESC LIMIT 1""",
            (system_id, row["feature_key"]),
        ).fetchone()
        if draft is not None:
            for ev in conn.execute(
                """SELECT path FROM draft_evidence
                   WHERE draft_type = 'feature' AND draft_id = ?
                   ORDER BY id""",
                (draft["id"],),
            ).fetchall():
                if ev["path"] not in paths:
                    paths.append(ev["path"])
                if len(paths) == 3:
                    break
        entries.append(
            RegistryEntry(
                feature_key=row["feature_key"],
                display_name=row["display_name"],
                summary=row["summary"] or "",
                handle=f"F{index}",
                evidence_paths=paths,
            )
        )
    return entries


def taken_keys(conn: sqlite3.Connection, system_id: int) -> Dict[str, str]:
    """Every key the System has used -> 'registered' | 'retired' | 'historical'."""
    taken: Dict[str, str] = {}
    for row in conn.execute(
        "SELECT DISTINCT feature_id FROM feature_drafts WHERE system_id = ?",
        (system_id,),
    ).fetchall():
        taken[row["feature_id"]] = "historical"
    for row in conn.execute(
        "SELECT feature_key, retired_at FROM feature_registry WHERE system_id = ?",
        (system_id,),
    ).fetchall():
        taken[row["feature_key"]] = "retired" if row["retired_at"] is not None else "registered"
    return taken


def record_run(
    conn: sqlite3.Connection,
    system_id: int,
    run_id: int,
    matched: Dict[str, str],
    minted: List[Dict[str, str]],
) -> None:
    """Persist a completed drafts run's outcome in the registry.

    ``matched`` maps an existing key to its latest summary; ``minted`` lists
    ``{"feature_key", "display_name", "summary"}`` for new keys.
    """
    now = time.time()
    for key, summary in matched.items():
        conn.execute(
            """UPDATE feature_registry
               SET summary = ?, updated_at = ?, last_run_id = ?
               WHERE system_id = ? AND feature_key = ?""",
            (summary, now, run_id, system_id, key),
        )
    for item in minted:
        conn.execute(
            """INSERT OR IGNORE INTO feature_registry
                   (system_id, feature_key, display_name, summary, origin,
                    created_at, updated_at, last_run_id)
               VALUES (?, ?, ?, ?, 'minted', ?, ?, ?)""",
            (system_id, item["feature_key"], item["display_name"],
             item.get("summary", ""), now, now, run_id),
        )
