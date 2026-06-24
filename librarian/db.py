"""SQLite + FTS5 database backend for the Sample Librarian.

Replaces the JSONL-based index/search with a proper relational store and
full-text search via SQLite FTS5.  All search hits are ranked with BM25.

Schema overview
---------------
samples          – one row per indexed audio file (UNIQUE on path)
tags             – many-to-many tags per sample (sample_id FK)
analysis_cache   – 1:1 audio-analysis results per sample (sample_id UNIQUE FK)
roots            – scanned root folders with last-scanned timestamp
scan_history     – audit log of scan runs
samples_fts      – FTS5 external-content table mirroring samples + tags

Usage::

    from librarian.db import get_db, search_samples, init_db

    init_db("data/samples.db")
    conn = get_db("data/samples.db")
    hits = search_samples(conn, "808 kick punchy", category="Kick")
    conn.close()
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_DB_PATH = "data/samples.db"

_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

_CREATE_SAMPLES = """
CREATE TABLE IF NOT EXISTS samples (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    path          TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL DEFAULT '',
    ext           TEXT NOT NULL DEFAULT '',
    size          INTEGER NOT NULL DEFAULT 0,
    category      TEXT NOT NULL DEFAULT '',
    folder        TEXT NOT NULL DEFAULT '',
    root          TEXT NOT NULL DEFAULT '',
    file_hash     TEXT,
    strings_json  TEXT NOT NULL DEFAULT '[]',
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""

_CREATE_TAGS = """
CREATE TABLE IF NOT EXISTS tags (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id   INTEGER NOT NULL REFERENCES samples(id) ON DELETE CASCADE,
    tag         TEXT NOT NULL,
    UNIQUE(sample_id, tag)
);
"""

_CREATE_TAGS_IDX_1 = """
CREATE INDEX IF NOT EXISTS idx_tags_sample_id ON tags(sample_id);
"""

_CREATE_TAGS_IDX_2 = """
CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag);
"""

_CREATE_ANALYSIS_CACHE = """
CREATE TABLE IF NOT EXISTS analysis_cache (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id         INTEGER NOT NULL UNIQUE REFERENCES samples(id) ON DELETE CASCADE,
    bpm               REAL,
    key               TEXT,
    pitch             TEXT,
    note_number       INTEGER,
    is_atonal         INTEGER NOT NULL DEFAULT 0,
    duration          REAL,
    sample_type       TEXT,
    spectral_centroid REAL,
    analysis_json     TEXT,
    analyzed_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""

_CREATE_ROOTS = """
CREATE TABLE IF NOT EXISTS roots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    path         TEXT NOT NULL UNIQUE,
    last_scanned TEXT,
    file_count   INTEGER NOT NULL DEFAULT 0
);
"""

_CREATE_SCAN_HISTORY = """
CREATE TABLE IF NOT EXISTS scan_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    root_path     TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    completed_at  TEXT,
    files_found   INTEGER NOT NULL DEFAULT 0,
    files_new     INTEGER NOT NULL DEFAULT 0,
    files_updated INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'running'
);
"""

# FTS5 external-content virtual table.
# Mirrors searchable text columns from *samples* plus a synthesised *tags_text*
# column that is maintained by helper / triggers.
_CREATE_SAMPLES_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS samples_fts USING fts5(
    name,
    category,
    folder,
    tags_text,
    content='samples',
    content_rowid='id',
    tokenize='unicode61'
);
"""

# ---------------------------------------------------------------------------
# FTS5 sync triggers on *samples*
#
# With the external-content pattern FTS5 must be kept in step manually.
# These three triggers handle INSERT / DELETE / UPDATE on the samples table.
# Note: *tags_text* is always NULL on the samples row (the column lives only in
# the FTS index), so the trigger uses a sub-select to assemble tag text.
# ---------------------------------------------------------------------------

_TRIGGER_SAMPLES_AI = """
CREATE TRIGGER IF NOT EXISTS samples_ai AFTER INSERT ON samples BEGIN
    INSERT INTO samples_fts(rowid, name, category, folder, tags_text)
    VALUES (
        new.id,
        COALESCE(new.name, ''),
        COALESCE(new.category, ''),
        COALESCE(new.folder, ''),
        (SELECT COALESCE(group_concat(tag, ' '), '') FROM tags WHERE sample_id = new.id)
    );
END;
"""

_TRIGGER_SAMPLES_AD = """
CREATE TRIGGER IF NOT EXISTS samples_ad AFTER DELETE ON samples BEGIN
    INSERT INTO samples_fts(samples_fts, rowid, name, category, folder, tags_text)
    VALUES ('delete', old.id, old.name, old.category, old.folder, '');
END;
"""

_TRIGGER_SAMPLES_AU = """
CREATE TRIGGER IF NOT EXISTS samples_au AFTER UPDATE ON samples BEGIN
    INSERT INTO samples_fts(samples_fts, rowid, name, category, folder, tags_text)
    VALUES ('delete', old.id, old.name, old.category, old.folder, '');
    INSERT INTO samples_fts(rowid, name, category, folder, tags_text)
    VALUES (
        new.id,
        COALESCE(new.name, ''),
        COALESCE(new.category, ''),
        COALESCE(new.folder, ''),
        (SELECT COALESCE(group_concat(tag, ' '), '') FROM tags WHERE sample_id = new.id)
    );
END;
"""

# ---------------------------------------------------------------------------
# FTS5 sync triggers on *tags*
#
# When tags are added, removed, or changed the tags_text column for the
# affected sample must be rebuilt in the FTS5 index.  Because FTS5 external-
# content tables do not support column-level UPDATE, we use the standard
# 'delete' + re-INSERT pattern to replace the entire indexed row.
# ---------------------------------------------------------------------------

_TRIGGER_TAGS_AI = """
CREATE TRIGGER IF NOT EXISTS tags_ai AFTER INSERT ON tags BEGIN
    INSERT INTO samples_fts(samples_fts, rowid, name, category, folder, tags_text)
    VALUES ('delete', new.sample_id,
        COALESCE((SELECT name FROM samples WHERE id = new.sample_id), ''),
        COALESCE((SELECT category FROM samples WHERE id = new.sample_id), ''),
        COALESCE((SELECT folder FROM samples WHERE id = new.sample_id), ''),
        '');
    INSERT INTO samples_fts(rowid, name, category, folder, tags_text)
    VALUES (new.sample_id,
        COALESCE((SELECT name FROM samples WHERE id = new.sample_id), ''),
        COALESCE((SELECT category FROM samples WHERE id = new.sample_id), ''),
        COALESCE((SELECT folder FROM samples WHERE id = new.sample_id), ''),
        COALESCE((SELECT group_concat(tag, ' ') FROM tags WHERE sample_id = new.sample_id), ''));
END;
"""

_TRIGGER_TAGS_AD = """
CREATE TRIGGER IF NOT EXISTS tags_ad AFTER DELETE ON tags BEGIN
    INSERT INTO samples_fts(samples_fts, rowid, name, category, folder, tags_text)
    VALUES ('delete', old.sample_id,
        COALESCE((SELECT name FROM samples WHERE id = old.sample_id), ''),
        COALESCE((SELECT category FROM samples WHERE id = old.sample_id), ''),
        COALESCE((SELECT folder FROM samples WHERE id = old.sample_id), ''),
        '');
    INSERT INTO samples_fts(rowid, name, category, folder, tags_text)
    VALUES (old.sample_id,
        COALESCE((SELECT name FROM samples WHERE id = old.sample_id), ''),
        COALESCE((SELECT category FROM samples WHERE id = old.sample_id), ''),
        COALESCE((SELECT folder FROM samples WHERE id = old.sample_id), ''),
        COALESCE((SELECT group_concat(tag, ' ') FROM tags WHERE sample_id = old.sample_id), ''));
END;
"""

_TRIGGER_TAGS_AU = """
CREATE TRIGGER IF NOT EXISTS tags_au AFTER UPDATE ON tags BEGIN
    INSERT INTO samples_fts(samples_fts, rowid, name, category, folder, tags_text)
    VALUES ('delete', new.sample_id,
        COALESCE((SELECT name FROM samples WHERE id = new.sample_id), ''),
        COALESCE((SELECT category FROM samples WHERE id = new.sample_id), ''),
        COALESCE((SELECT folder FROM samples WHERE id = new.sample_id), ''),
        '');
    INSERT INTO samples_fts(rowid, name, category, folder, tags_text)
    VALUES (new.sample_id,
        COALESCE((SELECT name FROM samples WHERE id = new.sample_id), ''),
        COALESCE((SELECT category FROM samples WHERE id = new.sample_id), ''),
        COALESCE((SELECT folder FROM samples WHERE id = new.sample_id), ''),
        COALESCE((SELECT group_concat(tag, ' ') FROM tags WHERE sample_id = new.sample_id), ''));
END;
"""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 (Z suffix)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_parent_dir(db_path: str) -> None:
    """Create parent directory for *db_path* if it does not exist."""
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _rebuild_tags_text(conn: sqlite3.Connection, sample_id: int) -> None:
    """Recompute the *tags_text* column in ``samples_fts`` for a sample.

    FTS5 external-content tables do not support column-level ``UPDATE``, so
    we replace the row using the standard 'delete' + re-INSERT pattern.

    Called from Python-side code paths (e.g. :func:`upsert_sample`) after
    bulk-replacing tags, to guarantee the FTS index reflects the latest tag set.
    """
    # Fetch the current sample + tag data in a single round-trip.
    row = conn.execute(
        """
        SELECT s.name, s.category, s.folder,
               (SELECT COALESCE(group_concat(t.tag, ' '), '')
                FROM tags t WHERE t.sample_id = s.id) AS tags_text
        FROM samples s WHERE s.id = ?
        """,
        (sample_id,),
    ).fetchone()
    if row is None:
        return
    name = row["name"] or ""
    category = row["category"] or ""
    folder = row["folder"] or ""
    tags_text = row["tags_text"] or ""

    # Remove old FTS entry and re-insert with fresh tags_text.
    conn.execute(
        "INSERT INTO samples_fts(samples_fts, rowid, name, category, folder, tags_text) "
        "VALUES ('delete', ?, ?, ?, ?, '')",
        (sample_id, name, category, folder),
    )
    conn.execute(
        "INSERT INTO samples_fts(rowid, name, category, folder, tags_text) "
        "VALUES (?, ?, ?, ?, ?)",
        (sample_id, name, category, folder, tags_text),
    )


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    """Convert a ``sqlite3.Row`` to a plain dict (or None)."""
    if row is None:
        return None
    return dict(row)


# ---------------------------------------------------------------------------
# Public API — connection / initialisation
# ---------------------------------------------------------------------------

def get_db(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open (or create) a database connection with sensible defaults.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.

    Returns
    -------
    sqlite3.Connection
        Connection with ``row_factory`` set to :class:`sqlite3.Row`,
        ``PRAGMA foreign_keys = ON`` and ``PRAGMA journal_mode = WAL``.
    """
    _ensure_parent_dir(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """Create all tables, triggers, and indexes if they don't already exist.

    Safe to call multiple times — every statement uses ``IF NOT EXISTS``.
    """
    conn = get_db(db_path)
    try:
        cur = conn.cursor()

        # --- Core tables --------------------------------------------------
        cur.execute(_CREATE_SAMPLES)
        cur.execute(_CREATE_TAGS)
        cur.execute(_CREATE_TAGS_IDX_1)
        cur.execute(_CREATE_TAGS_IDX_2)
        cur.execute(_CREATE_ANALYSIS_CACHE)
        cur.execute(_CREATE_ROOTS)
        cur.execute(_CREATE_SCAN_HISTORY)

        # --- FTS5 virtual table ------------------------------------------
        cur.execute(_CREATE_SAMPLES_FTS)

        # --- Triggers (samples ↔ FTS5) -----------------------------------
        cur.execute(_TRIGGER_SAMPLES_AI)
        cur.execute(_TRIGGER_SAMPLES_AD)
        cur.execute(_TRIGGER_SAMPLES_AU)

        # --- Triggers (tags ↔ FTS5 tags_text) ----------------------------
        cur.execute(_TRIGGER_TAGS_AI)
        cur.execute(_TRIGGER_TAGS_AD)
        cur.execute(_TRIGGER_TAGS_AU)

        # --- Convenience indexes -----------------------------------------
        cur.execute("CREATE INDEX IF NOT EXISTS idx_samples_category ON samples(category);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_samples_ext ON samples(ext);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_samples_root ON samples(root);")

        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Public API — sample CRUD
# ---------------------------------------------------------------------------

def upsert_sample(conn: sqlite3.Connection, record: dict[str, Any]) -> int:
    """Insert or update a sample record and return its ``sample_id``.

    Expected keys in *record* (missing values default to empty / zero):

    ``path`` (required), ``name``, ``ext``, ``size``, ``category``,
    ``folder``, ``root``, ``file_hash``, ``strings`` (list[str]),
    ``tags`` (list[str]).
    """
    if not record or not record.get("path"):
        raise ValueError("record must contain a non-empty 'path' key")

    path = record["path"]
    name = record.get("name", "") or ""
    ext = record.get("ext", "") or ""
    size = int(record.get("size", 0) or 0)
    category = record.get("category", "") or ""
    folder = record.get("folder", "") or ""
    root = record.get("root", "") or ""
    file_hash = record.get("file_hash")
    strings = record.get("strings", [])
    strings_json = json.dumps(strings, ensure_ascii=False) if strings else "[]"
    tags = record.get("tags", []) or []

    now = _now_iso()

    # Try INSERT … ON CONFLICT for upsert (SQLite ≥ 3.24.0).
    conn.execute(
        """
        INSERT INTO samples (path, name, ext, size, category, folder, root,
                             file_hash, strings_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            name          = excluded.name,
            ext           = excluded.ext,
            size          = excluded.size,
            category      = excluded.category,
            folder        = excluded.folder,
            root          = excluded.root,
            file_hash     = excluded.file_hash,
            strings_json  = excluded.strings_json,
            updated_at    = excluded.updated_at
        """,
        (path, name, ext, size, category, folder, root, file_hash,
         strings_json, now, now),
    )

    row = conn.execute("SELECT id FROM samples WHERE path = ?", (path,)).fetchone()
    sample_id = row[0]

    # --- Sync tags ----------------------------------------------------
    _replace_tags(conn, sample_id, tags)

    # --- Rebuild FTS tags_text (belt-and-suspenders alongside trigger) -
    _rebuild_tags_text(conn, sample_id)

    conn.commit()
    return sample_id


def _replace_tags(conn: sqlite3.Connection, sample_id: int, tags: list[str]) -> None:
    """Replace the full set of tags for *sample_id*."""
    conn.execute("DELETE FROM tags WHERE sample_id = ?", (sample_id,))
    if tags:
        # Deduplicate while preserving order.
        seen: set[str] = set()
        unique_tags = []
        for t in tags:
            t = t.strip()
            if t and t not in seen:
                seen.add(t)
                unique_tags.append(t)
        conn.executemany(
            "INSERT OR IGNORE INTO tags (sample_id, tag) VALUES (?, ?)",
            [(sample_id, t) for t in unique_tags],
        )


def upsert_analysis(conn: sqlite3.Connection, sample_id: int, analysis: dict[str, Any]) -> None:
    """Insert or update cached audio-analysis results for a sample.

    Recognised keys (all optional): ``bpm``, ``key``, ``pitch``,
    ``note_number``, ``is_atonal``, ``duration``, ``sample_type``,
    ``spectral_centroid``.  Any extra keys are preserved inside
    ``analysis_json``.
    """
    if not analysis:
        analysis = {}

    bpm = analysis.get("bpm")
    key = analysis.get("key") or analysis.get("estimated_key_root")
    pitch = analysis.get("pitch") or analysis.get("note_name")
    note_number = analysis.get("note_number")
    is_atonal = 1 if analysis.get("is_atonal", False) else 0
    duration = analysis.get("duration")
    sample_type = analysis.get("sample_type")
    spectral_centroid = analysis.get("spectral_centroid")
    analysis_json = json.dumps(analysis, ensure_ascii=False)
    now = _now_iso()

    conn.execute(
        """
        INSERT INTO analysis_cache
            (sample_id, bpm, key, pitch, note_number, is_atonal,
             duration, sample_type, spectral_centroid, analysis_json, analyzed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(sample_id) DO UPDATE SET
            bpm               = excluded.bpm,
            key               = excluded.key,
            pitch             = excluded.pitch,
            note_number       = excluded.note_number,
            is_atonal         = excluded.is_atonal,
            duration          = excluded.duration,
            sample_type       = excluded.sample_type,
            spectral_centroid = excluded.spectral_centroid,
            analysis_json     = excluded.analysis_json,
            analyzed_at       = excluded.analyzed_at
        """,
        (sample_id, bpm, key, pitch, note_number, is_atonal, duration,
         sample_type, spectral_centroid, analysis_json, now),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Public API — queries
# ---------------------------------------------------------------------------

def _run_fts(
    conn: sqlite3.Connection,
    fts_query: str,
    category: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Run a single FTS5 MATCH query and return sample dicts.

    *fts_query* is passed verbatim to FTS5.  *category* is applied as a
    case-insensitive filter when given.
    """
    sql = """
        SELECT s.*, bm25(samples_fts) AS rank
        FROM samples_fts
        JOIN samples s ON s.id = samples_fts.rowid
        WHERE samples_fts MATCH ?
    """
    params: list[Any] = [fts_query]
    if category:
        sql += " AND lower(s.category) = lower(?)"
        params.append(category)
    sql += " ORDER BY rank ASC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        d = dict(row)
        sid = d["id"]
        d["tags"] = get_sample_tags(conn, sid)
        results.append(d)
    return results


def search_samples(
    conn: sqlite3.Connection,
    query: str,
    category: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Full-text search across sample name, category, folder, and tags.

    Search runs in two stages:

    1. **AND** — all tokens must match (precise).  Each token is quoted so
       FTS5 combines them with implicit AND.
    2. **OR fallback** — if the AND stage returns nothing, tokens are joined
       with ``OR`` so samples matching *any* token are returned, ranked by
       BM25 (samples containing more query tokens rank higher).

    Parameters
    ----------
    query:
        Raw search string.
    category:
        If given, results are filtered to this category (case-insensitive).
    limit:
        Maximum number of results.

    Returns
    -------
    list[dict]
        Each dict has all columns from ``samples`` plus ``rank`` (BM25 score)
        and ``tags`` (list[str]).
    """
    if not query or not query.strip():
        return []

    tokens = [t.strip() for t in query.split() if t.strip()]
    if not tokens:
        return []

    def _quote(tok: str) -> str:
        return '"' + tok.replace('"', '""') + '"'

    # Stage 1: AND (precise)
    fts_and = " ".join(_quote(t) for t in tokens)
    results = _run_fts(conn, fts_and, category, limit)
    if results:
        return results

    # Stage 2: OR fallback (relaxed)
    if len(tokens) == 1:
        return []  # OR on a single token is identical to AND; skip redundant query
    fts_or = " OR ".join(_quote(t) for t in tokens)
    return _run_fts(conn, fts_or, category, limit)


def get_sample_by_path(conn: sqlite3.Connection, path: str) -> dict[str, Any] | None:
    """Fetch a single sample (and its tags) by absolute file path."""
    if not path:
        return None
    row = conn.execute("SELECT * FROM samples WHERE path = ?", (path,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["tags"] = get_sample_tags(conn, d["id"])
    if d.get("strings_json"):
        try:
            d["strings"] = json.loads(d["strings_json"])
        except (json.JSONDecodeError, TypeError):
            d["strings"] = []
    else:
        d["strings"] = []
    return d


def get_sample_tags(conn: sqlite3.Connection, sample_id: int) -> list[str]:
    """Return the list of tags for a sample (ordered by insertion)."""
    rows = conn.execute(
        "SELECT tag FROM tags WHERE sample_id = ? ORDER BY id", (sample_id,)
    ).fetchall()
    return [r[0] for r in rows] if rows else []


# ---------------------------------------------------------------------------
# Public API — scan tracking
# ---------------------------------------------------------------------------

def record_scan(
    conn: sqlite3.Connection,
    root_path: str,
    files_found: int,
    files_new: int,
    files_updated: int,
) -> None:
    """Record a completed (or running) scan in ``scan_history``.

    A row is inserted with ``status='completed'`` and ``completed_at=now``.
    """
    now = _now_iso()
    conn.execute(
        """
        INSERT INTO scan_history
            (root_path, started_at, completed_at, files_found,
             files_new, files_updated, status)
        VALUES (?, ?, ?, ?, ?, ?, 'completed')
        """,
        (root_path, now, now, files_found, files_new, files_updated),
    )
    conn.commit()


def update_root(conn: sqlite3.Connection, root_path: str, file_count: int) -> None:
    """Upsert a root folder record with the latest file count and timestamp."""
    now = _now_iso()
    conn.execute(
        """
        INSERT INTO roots (path, last_scanned, file_count)
        VALUES (?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            last_scanned = excluded.last_scanned,
            file_count   = excluded.file_count
        """,
        (root_path, now, file_count),
    )
    conn.commit()


def compute_file_hash(path: str) -> str | None:
    """Quick hash of file path + size for dedup.

    Shared by :func:`scan_root_to_db` and ``batch_analyze_sqlite.py`` so the
    dedup scheme stays consistent across ingestion paths.
    """
    import hashlib

    try:
        st = os.stat(path)
        return hashlib.md5(f"{path}:{st.st_size}".encode()).hexdigest()
    except OSError:
        return None


def scan_root_to_db(
    conn: sqlite3.Connection,
    root_path: str | Path,
    scan_presets: bool = True,
) -> dict[str, Any]:
    """Scan a folder and upsert all audio/preset files into the samples table.

    Reuses :func:`librarian.index._scan_folder` to build records (name, path,
    ext, size, category, folder, root, tags, strings), adds a ``file_hash``,
    and persists each via :func:`upsert_sample`.  Audio analysis (librosa)
    is **not** performed here — that is a separate step
    (``batch_analyze_sqlite.py`` / ``librarian_analyze``).

    Updates the ``roots`` and ``scan_history`` tables.

    Parameters
    ----------
    root_path:
        Folder to scan recursively.
    scan_presets:
        Include preset files (.nmsv, .nksf, etc.).

    Returns
    -------
    dict
        ``{"files_found": int, "files_new": int, "files_updated": int,
        "root": str}``.
    """
    # Late import to avoid any circular dependency with librarian.index.
    from .index import _scan_folder

    root = Path(root_path)
    if not root.exists():
        return {"files_found": 0, "files_new": 0, "files_updated": 0,
                "root": str(root)}

    # Snapshot existing paths so we can count new vs updated.
    existing = {
        row[0] for row in conn.execute("SELECT path FROM samples").fetchall()
    }

    files_new = 0
    files_updated = 0
    found = 0
    for record in _scan_folder(root, scan_presets=scan_presets):
        found += 1
        record["file_hash"] = compute_file_hash(record["path"])
        upsert_sample(conn, record)
        if record["path"] in existing:
            files_updated += 1
        else:
            files_new += 1

    record_scan(conn, str(root), found, files_new, files_updated)
    update_root(conn, str(root), found)

    return {"files_found": found, "files_new": files_new,
            "files_updated": files_updated, "root": str(root)}


def recommend_samples_db(
    conn: sqlite3.Connection,
    target_key: str,
    terms: list[str] | None = None,
    category: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Recommend samples harmonically compatible with *target_key*.

    Uses Camelot Wheel matching via :func:`librarian.analyze.get_compatible_keys`
    against keys stored in ``analysis_cache``.  No real-time analysis is
    performed — only previously-analysed samples are considered.

    Selection rules:

    * Samples with ``is_atonal = 1`` (hi-hats, noise, FX) are **always**
      included — they have no key constraint.
    * Tonal samples are included only if their ``key`` is in the compatible
      set for *target_key*.
    * Samples without analysis data (no ``analysis_cache`` row) are excluded.

    *terms* and *category* narrow the candidate pool via FTS5 search before
    key filtering.

    Parameters
    ----------
    target_key:
        Target key, e.g. ``"Fm"``, ``"C"``, ``"Am"``.
    terms:
        Optional search terms to filter candidates.
    category:
        Optional category filter.
    limit:
        Maximum results.

    Returns
    -------
    list[dict]
        Enriched sample dicts (via :func:`enrich_result`), preserving
        candidate order.
    """
    from .analyze import get_compatible_keys

    compatible_keys = set(get_compatible_keys(target_key))

    # Gather candidates via FTS5 if terms given, else scan broadly.
    query = " ".join(terms) if terms else ""
    if query:
        candidates = search_samples(conn, query, category=category, limit=limit * 5)
    else:
        # No terms: fetch recent samples, optionally filtered by category.
        if category:
            rows = conn.execute(
                "SELECT * FROM samples WHERE lower(category) = lower(?) "
                "ORDER BY updated_at DESC LIMIT ?",
                (category, limit * 5),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM samples ORDER BY updated_at DESC LIMIT ?",
                (limit * 5,),
            ).fetchall()
        candidates = []
        for row in rows:
            d = dict(row)
            d["tags"] = get_sample_tags(conn, d["id"])
            candidates.append(d)

    if not candidates:
        return []

    # Batch-fetch analysis for candidates.
    sample_ids = [c["id"] for c in candidates if "id" in c]
    analysis_map: dict[int, dict] = {}
    if sample_ids:
        placeholders = ",".join("?" * len(sample_ids))
        rows = conn.execute(
            f"SELECT * FROM analysis_cache WHERE sample_id IN ({placeholders})",
            sample_ids,
        ).fetchall()
        for row in rows:
            d = dict(row)
            analysis_map[d["sample_id"]] = d

    compatible: list[dict[str, Any]] = []
    for c in candidates:
        sid = c.get("id")
        analysis = analysis_map.get(sid) if sid else None
        if analysis is None:
            continue  # no analysis → cannot determine key → skip
        is_atonal = bool(analysis.get("is_atonal", 0))
        if is_atonal:
            compatible.append(enrich_result(c, analysis))
            continue
        key_val = analysis.get("key")
        if key_val and key_val in compatible_keys:
            compatible.append(enrich_result(c, analysis))

    return compatible[:limit]


# ---------------------------------------------------------------------------
# Public API — stats
# ---------------------------------------------------------------------------

def get_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return summary statistics about the database.

    Keys: ``total_samples``, ``by_category`` (dict),
    ``by_ext`` (dict), ``analyzed_count``.
    """
    total_row = conn.execute("SELECT COUNT(*) FROM samples").fetchone()
    total_samples = total_row[0] if total_row else 0

    cat_rows = conn.execute(
        "SELECT category, COUNT(*) AS cnt FROM samples GROUP BY category ORDER BY cnt DESC"
    ).fetchall()
    by_category = {r[0]: r[1] for r in cat_rows}

    ext_rows = conn.execute(
        "SELECT ext, COUNT(*) AS cnt FROM samples GROUP BY ext ORDER BY cnt DESC"
    ).fetchall()
    by_ext = {r[0]: r[1] for r in ext_rows}

    analyzed_row = conn.execute("SELECT COUNT(*) FROM analysis_cache").fetchone()
    analyzed_count = analyzed_row[0] if analyzed_row else 0

    return {
        "total_samples": total_samples,
        "by_category": by_category,
        "by_ext": by_ext,
        "analyzed_count": analyzed_count,
    }


# ---------------------------------------------------------------------------
# Public API — migration
# ---------------------------------------------------------------------------

def migrate_from_jsonl(conn: sqlite3.Connection, jsonl_path: str) -> int:
    """Migrate records from a legacy JSONL index into the database.

    Each line is a JSON object matching the record format produced by
    :mod:`librarian.index`.  Existing samples (matched by ``path``) are
    updated in place.

    Returns the number of records migrated.
    """
    path = Path(jsonl_path)
    if not path.exists():
        return 0

    count = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not record.get("path"):
                continue
            upsert_sample(conn, record)
            count += 1

    return count


# ---------------------------------------------------------------------------
# Public API — AI-friendly enrichment
# ---------------------------------------------------------------------------

# Category → recommended use mapping
_CATEGORY_USE_MAP = {
    "Kick": "drum_foundation",
    "Snare": "drum_foundation",
    "HiHat": "drum_foundation",
    "Clap": "drum_foundation",
    "Cymbal": "drum_foundation",
    "Tom": "drum_foundation",
    "Percussion": "drum_foundation",
    "Shaker": "groove_layer",
    "Bass": "bassline",
    "Sub": "sub_layer",
    "Lead": "melody",
    "Synth": "melody",
    "Pad": "harmony",
    "Pads": "harmony",
    "FX": "transition",
    "Vocal": "vocal_chop",
    "Loop": "full_loop",
    "OneShot": "single_hit",
}

# Category → Ableton action suggestion
_CATEGORY_ACTION_MAP = {
    "Kick": "load_sample_to_pad(track_index, pad_index=36, file_path)",
    "Snare": "load_sample_to_pad(track_index, pad_index=38, file_path)",
    "HiHat": "load_sample_to_pad(track_index, pad_index=42, file_path)",
    "Clap": "load_sample_to_pad(track_index, pad_index=39, file_path)",
    "Cymbal": "load_sample_to_pad(track_index, pad_index=49, file_path)",
    "Tom": "load_sample_to_pad(track_index, pad_index=45, file_path)",
    "Percussion": "load_sample_to_pad(track_index, pad_index=40, file_path)",
    "Shaker": "load_sample_to_pad(track_index, pad_index=44, file_path)",
    "Bass": "import_audio_clip(track_index, slot_index, file_path)",
    "Sub": "import_audio_clip(track_index, slot_index, file_path)",
    "Lead": "import_audio_clip(track_index, slot_index, file_path)",
    "Synth": "import_audio_clip(track_index, slot_index, file_path)",
    "Pad": "import_audio_clip(track_index, slot_index, file_path)",
    "Pads": "import_audio_clip(track_index, slot_index, file_path)",
    "FX": "import_audio_clip(track_index, slot_index, file_path)",
    "Vocal": "import_audio_clip(track_index, slot_index, file_path)",
    "Loop": "import_audio_clip(track_index, slot_index, file_path)",
    "OneShot": "load_sample_to_pad(track_index, pad_index=36, file_path)",
}


def enrich_result(sample: dict[str, Any], analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    """Enrich a raw sample dict with AI-friendly fields.

    Adds:
    - ``key`` — detected musical key (from analysis_cache)
    - ``bpm`` — detected tempo
    - ``sample_type`` — oneshot / short_loop / medium_loop / long_loop
    - ``pitch`` — detected pitch note name
    - ``is_atonal`` — bool (true for non-pitched samples like hi-hats)
    - ``confidence`` — heuristic confidence score (0.0–1.0)
    - ``recommended_use`` — how to use this sample in a track
    - ``ableton_action`` — suggested LiveAgent tool call
    - ``compatible_keys`` — list of harmonically compatible keys (if key known)

    Parameters
    ----------
    sample:
        Raw sample dict from search_samples or get_sample_by_path.
    analysis:
        Optional analysis_cache row dict.  If None, looks up from DB.
    """
    category = sample.get("category", "Other")

    # Merge analysis fields if available
    a = analysis or {}
    key_val = a.get("key") or a.get("estimated_key_root")
    bpm_val = a.get("bpm")
    pitch_val = a.get("pitch") or a.get("note_name")
    note_number = a.get("note_number")
    is_atonal = bool(a.get("is_atonal", False))
    duration = a.get("duration")
    sample_type = a.get("sample_type")
    spectral_centroid = a.get("spectral_centroid")

    # Confidence heuristic
    confidence = 0.0
    if key_val:
        confidence += 0.4
    if bpm_val:
        confidence += 0.3
    if pitch_val and not is_atonal:
        confidence += 0.2
    if spectral_centroid is not None:
        confidence += 0.1
    confidence = round(min(confidence, 1.0), 2)

    # Recommended use
    recommended_use = _CATEGORY_USE_MAP.get(category, "general")

    # Ableton action suggestion
    ableton_action = _CATEGORY_ACTION_MAP.get(category, "import_audio_clip(track_index, slot_index, file_path)")

    # Compatible keys (Camelot Wheel)
    compatible_keys: list[str] = []
    if key_val:
        try:
            from .analyze import get_compatible_keys
            compatible_keys = get_compatible_keys(key_val)
        except Exception:
            pass

    enriched = {
        **sample,
        "key": key_val,
        "bpm": bpm_val,
        "pitch": pitch_val,
        "note_number": note_number,
        "is_atonal": is_atonal,
        "duration": duration,
        "sample_type": sample_type,
        "confidence": confidence,
        "recommended_use": recommended_use,
        "ableton_action": ableton_action,
        "compatible_keys": compatible_keys,
    }
    return enriched


def search_samples_enriched(
    conn: sqlite3.Connection,
    query: str,
    category: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search and return AI-friendly enriched results.

    Combines FTS5 search with analysis_cache JOIN to produce results that
    include key, BPM, sample_type, confidence, recommended_use, and
    ableton_action — optimised for agent decision-making.
    """
    results = search_samples(conn, query, category=category, limit=limit)
    if not results:
        return []

    # Batch-fetch analysis for all result IDs
    sample_ids = [r["id"] for r in results if "id" in r]
    analysis_map: dict[int, dict] = {}
    if sample_ids:
        placeholders = ",".join("?" * len(sample_ids))
        rows = conn.execute(
            f"SELECT * FROM analysis_cache WHERE sample_id IN ({placeholders})",
            sample_ids,
        ).fetchall()
        for row in rows:
            d = dict(row)
            analysis_map[d["sample_id"]] = d

    enriched: list[dict[str, Any]] = []
    for r in results:
        sid = r.get("id")
        analysis = analysis_map.get(sid) if sid else None
        enriched.append(enrich_result(r, analysis))

    return enriched


# ---------------------------------------------------------------------------
# Public API — duplicate / similarity detection
# ---------------------------------------------------------------------------


def find_duplicates_by_hash(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Find exact duplicates by file_hash.

    Returns groups of samples sharing the same file_hash (content-identical).
    Each group dict has: hash, count, samples (list of sample dicts).
    """
    rows = conn.execute(
        """
        SELECT file_hash, COUNT(*) as cnt
        FROM samples
        WHERE file_hash IS NOT NULL AND file_hash != ''
        GROUP BY file_hash
        HAVING cnt > 1
        ORDER BY cnt DESC
        """
    ).fetchall()

    groups: list[dict[str, Any]] = []
    for row in rows:
        h = row[0]
        sample_rows = conn.execute(
            "SELECT * FROM samples WHERE file_hash = ? ORDER BY path", (h,)
        ).fetchall()
        samples = [dict(r) for r in sample_rows]
        for s in samples:
            s["tags"] = get_sample_tags(conn, s["id"])
        groups.append({"hash": h, "count": len(samples), "samples": samples})

    return groups


def find_similar_by_duration(
    conn: sqlite3.Connection,
    tolerance: float = 0.05,
) -> list[dict[str, Any]]:
    """Find samples with near-identical durations (potential duplicates).

    Groups samples whose durations are within ``tolerance`` seconds of each
    other AND share the same category.  Uses analysis_cache for duration data.
    """
    rows = conn.execute(
        """
        SELECT s.id, s.path, s.name, s.category, s.size, a.duration
        FROM samples s
        JOIN analysis_cache a ON a.sample_id = s.id
        WHERE a.duration IS NOT NULL AND a.duration > 0
        ORDER BY s.category, a.duration
        """
    ).fetchall()

    # Group by category + similar duration
    groups: list[dict[str, Any]] = []
    current_group: list[dict] = []
    current_cat: str | None = None
    current_dur: float | None = None

    for row in rows:
        d = dict(row)
        cat = d.get("category", "")
        dur = d.get("duration", 0)

        if cat != current_cat or current_dur is None or abs(dur - current_dur) > tolerance:
            if len(current_group) > 1:
                groups.append({
                    "category": current_cat,
                    "duration_range": [current_group[0]["duration"], current_group[-1]["duration"]],
                    "count": len(current_group),
                    "samples": current_group,
                })
            current_group = [d]
            current_cat = cat
            current_dur = dur
        else:
            current_group.append(d)
            current_dur = dur

    # Flush last group
    if len(current_group) > 1:
        groups.append({
            "category": current_cat,
            "duration_range": [current_group[0]["duration"], current_group[-1]["duration"]],
            "count": len(current_group),
            "samples": current_group,
        })

    return groups


def find_similar_by_pitch(
    conn: sqlite3.Connection,
    same_category: bool = True,
) -> list[dict[str, Any]]:
    """Find samples with the same pitch and sample_type.

    Groups non-atonal samples by (note_number, sample_type).  Useful for
    finding multiple kicks at the same pitch, snares at the same pitch, etc.
    """
    sql = """
        SELECT s.id, s.path, s.name, s.category, s.size,
               a.note_number, a.pitch, a.sample_type, a.is_atonal
        FROM samples s
        JOIN analysis_cache a ON a.sample_id = s.id
        WHERE a.note_number IS NOT NULL AND a.is_atonal = 0
    """
    if same_category:
        sql += " ORDER BY s.category, a.note_number"
    else:
        sql += " ORDER BY a.note_number, s.category"

    rows = conn.execute(sql).fetchall()

    # Group by (note_number, sample_type)
    from collections import defaultdict
    key_map: dict[tuple, list[dict]] = defaultdict(list)

    for row in rows:
        d = dict(row)
        key = (d["note_number"], d.get("sample_type", ""))
        if same_category:
            key = (d.get("category", ""),) + key
        key_map[key].append(d)

    groups: list[dict[str, Any]] = []
    for key, samples in key_map.items():
        if len(samples) > 1:
            group: dict[str, Any] = {
                "count": len(samples),
                "samples": samples,
            }
            if same_category:
                group["category"] = key[0]
                group["note_number"] = key[1]
                group["pitch"] = samples[0].get("pitch")
                group["sample_type"] = key[2]
            else:
                group["note_number"] = key[0]
                group["pitch"] = samples[0].get("pitch")
                group["sample_type"] = key[1]
            groups.append(group)

    groups.sort(key=lambda g: g["count"], reverse=True)
    return groups


def find_similar_by_spectral(
    conn: sqlite3.Connection,
    tolerance: float = 50.0,
) -> list[dict[str, Any]]:
    """Find samples with similar spectral centroid (similar timbre).

    Groups samples whose spectral centroids are within ``tolerance`` Hz of
    each other AND share the same category.  Requires analysis_cache to have
    spectral_centroid populated.
    """
    rows = conn.execute(
        """
        SELECT s.id, s.path, s.name, s.category, s.size,
               a.spectral_centroid, a.sample_type
        FROM samples s
        JOIN analysis_cache a ON a.sample_id = s.id
        WHERE a.spectral_centroid IS NOT NULL
        ORDER BY s.category, a.spectral_centroid
        """
    ).fetchall()

    groups: list[dict[str, Any]] = []
    current_group: list[dict] = []
    current_cat: str | None = None
    current_centroid: float | None = None

    for row in rows:
        d = dict(row)
        cat = d.get("category", "")
        centroid = d.get("spectral_centroid", 0)

        if cat != current_cat or current_centroid is None or abs(centroid - current_centroid) > tolerance:
            if len(current_group) > 1:
                groups.append({
                    "category": current_cat,
                    "centroid_range": [current_group[0]["spectral_centroid"], current_group[-1]["spectral_centroid"]],
                    "count": len(current_group),
                    "samples": current_group,
                })
            current_group = [d]
            current_cat = cat
            current_centroid = centroid
        else:
            current_group.append(d)
            current_centroid = centroid

    if len(current_group) > 1:
        groups.append({
            "category": current_cat,
            "centroid_range": [current_group[0]["spectral_centroid"], current_group[-1]["spectral_centroid"]],
            "count": len(current_group),
            "samples": current_group,
        })

    return groups


def find_all_duplicates(
    conn: sqlite3.Connection,
) -> dict[str, list[dict[str, Any]]]:
    """Run all duplicate/similarity checks and return a combined report.

    Returns dict with keys: by_hash, by_duration, by_pitch, by_spectral.
    """
    return {
        "by_hash": find_duplicates_by_hash(conn),
        "by_duration": find_similar_by_duration(conn),
        "by_pitch": find_similar_by_pitch(conn),
        "by_spectral": find_similar_by_spectral(conn),
    }


# ---------------------------------------------------------------------------
# Public API — teardown
# ---------------------------------------------------------------------------

def close_db(conn: sqlite3.Connection) -> None:
    """Commit any pending transaction and close the connection safely."""
    with contextlib.suppress(sqlite3.Error):
        conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _main(argv: list[str] | None = None) -> int:
    """Initialise the database and optionally migrate from JSONL."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Initialise / migrate the Sample Librarian SQLite database.",
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help=f"Database path (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--jsonl",
        default="data/samples_index.jsonl",
        help="JSONL index to migrate from (skipped if file doesn't exist)",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print database stats after initialisation / migration",
    )
    args = parser.parse_args(argv)

    db_path = os.path.expanduser(args.db) if args.db.startswith("~") else args.db
    jsonl_path = os.path.expanduser(args.jsonl) if args.jsonl.startswith("~") else args.jsonl

    print(f"Initialising database at {db_path} ...", file=sys.stderr)
    init_db(db_path)

    conn = get_db(db_path)
    try:
        if os.path.exists(jsonl_path):
            print(f"Migrating from {jsonl_path} ...", file=sys.stderr)
            migrated = migrate_from_jsonl(conn, jsonl_path)
            print(f"  Migrated {migrated} records.", file=sys.stderr)
        else:
            print(f"No JSONL at {jsonl_path} — skipping migration.", file=sys.stderr)

        if args.stats:
            stats = get_stats(conn)
            print(file=sys.stderr)
            print("Database statistics:", file=sys.stderr)
            print(f"  Total samples:  {stats['total_samples']}", file=sys.stderr)
            print(f"  Analyzed:       {stats['analyzed_count']}", file=sys.stderr)
            print(f"  Categories:     {len(stats['by_category'])}", file=sys.stderr)
            print(f"  Extensions:     {len(stats['by_ext'])}", file=sys.stderr)
            top_cats = list(stats["by_category"].items())[:8]
            if top_cats:
                cat_str = ", ".join(f"{k}({v})" for k, v in top_cats)
                print(f"  Top categories: {cat_str}", file=sys.stderr)
    finally:
        close_db(conn)

    print("Done.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
