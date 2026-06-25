"""Tests for librarian.db — SQLite/FTS5 backend (no Ableton required).

Covers: init_db, upsert_sample, FTS5 search, category filter, tags,
analysis cache, enrichment, duplicate/similarity detection, stats, migration.
"""

from __future__ import annotations

import json
from pathlib import Path

from librarian.db import (  # noqa: I001
    enrich_result,
    find_duplicates_by_hash,
    find_similar_by_duration,
    find_similar_by_pitch,
    get_db,
    get_sample_by_path,
    get_stats,
    init_db,
    migrate_from_jsonl,
    search_samples,
    search_samples_enriched,
    upsert_analysis,
    upsert_sample,
)

# ---------------------------------------------------------------------------
# Schema / init
# ---------------------------------------------------------------------------

def test_init_db(tmp_path: Path):
    """init_db creates all expected tables."""
    db_path = str(tmp_path / "init_test.db")
    init_db(db_path)

    conn = get_db(db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        conn.close()

    expected = {"samples", "tags", "analysis_cache", "roots", "scan_history"}
    assert expected.issubset(tables)
    # FTS5 virtual table
    assert "samples_fts" in tables


# ---------------------------------------------------------------------------
# Upsert (insert + update)
# ---------------------------------------------------------------------------

def test_upsert_sample(db_conn, sample_factory):
    """Insert a sample and retrieve it by path."""
    sid = sample_factory(
        db_conn,
        name="Punchy Kick",
        category="Kick",
        tags=["808", "punchy"],
    )
    assert isinstance(sid, int)
    assert sid > 0

    result = get_sample_by_path(db_conn, "/test/punchy_kick.wav")
    assert result is not None
    assert result["name"] == "Punchy Kick"
    assert result["category"] == "Kick"
    assert "808" in result["tags"]
    assert "punchy" in result["tags"]


def test_upsert_sample_update(db_conn, sample_factory):
    """Upserting the same path updates the record in place."""
    sid1 = sample_factory(
        db_conn,
        name="Old Name",
        category="Kick",
        path="/shared/path.wav",
    )
    # Upsert with new values on the same path
    sid2 = upsert_sample(db_conn, {
        "path": "/shared/path.wav",
        "name": "New Name",
        "ext": "wav",
        "size": 2048,
        "category": "Snare",
        "folder": "/new",
        "root": "/new",
        "tags": ["updated"],
    })
    assert sid2 == sid1  # same row id

    result = get_sample_by_path(db_conn, "/shared/path.wav")
    assert result is not None
    assert result["name"] == "New Name"
    assert result["category"] == "Snare"
    assert result["tags"] == ["updated"]


# ---------------------------------------------------------------------------
# FTS5 search
# ---------------------------------------------------------------------------

def test_fts5_search(db_conn, sample_factory):
    """Each sample is findable by its own name."""
    sample_factory(db_conn, name="Deep Kick", category="Kick", path="/s/kick.wav")
    sample_factory(db_conn, name="Sharp Snare", category="Snare", path="/s/snare.wav")
    sample_factory(db_conn, name="Closed Hat", category="HiHat", path="/s/hat.wav")

    for term, expected_cat in [("Kick", "Kick"), ("Snare", "Snare"), ("Hat", "HiHat")]:
        results = search_samples(db_conn, term)
        assert len(results) >= 1
        assert any(r["category"] == expected_cat for r in results)


def test_fts5_search_multiword(db_conn, sample_factory):
    """Multi-word queries AND-combine via FTS5."""
    sample_factory(
        db_conn, name="808 Kick Deep", category="Kick",
        path="/s/multi1.wav", tags=["punchy"],
    )
    sample_factory(
        db_conn, name="Acoustic Snare", category="Snare",
        path="/s/multi2.wav",
    )

    # "deep kick" should match the kick but not the snare
    results = search_samples(db_conn, "deep kick")
    assert len(results) >= 1
    names = [r["name"] for r in results]
    assert any("Kick" in n for n in names)
    assert not any("Snare" in n for n in names)


def test_fts5_search_or_fallback(db_conn, sample_factory):
    """AND検索で0件の時、OR検索にフォールバックして結果を返す。"""
    sample_factory(
        db_conn, name="808 Kick", category="Kick",
        path="/s/or1.wav", tags=["punchy"],
    )
    sample_factory(
        db_conn, name="Punchy Snare", category="Snare",
        path="/s/or2.wav",
    )

    # "808 snare" — 両方の単語を含むサンプルはないが、
    # ORフォールバックで各サンプルが1語ずつマッチするはず（両方返る）
    results = search_samples(db_conn, "808 snare")
    names = [r["name"] for r in results]
    assert "808 Kick" in names and "Punchy Snare" in names


# ---------------------------------------------------------------------------
# Category filter
# ---------------------------------------------------------------------------

def test_category_filter(db_conn, sample_factory):
    """search_samples with category filters correctly."""
    sample_factory(db_conn, name="Boom Kick", category="Kick", path="/c/kick.wav")
    sample_factory(db_conn, name="Boom Snare", category="Snare", path="/c/snare.wav")

    # "Boom" matches both, but filtering by category=Kick limits to 1
    kick_only = search_samples(db_conn, "Boom", category="Kick")
    assert len(kick_only) == 1
    assert kick_only[0]["category"] == "Kick"


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

def test_tags_insert_and_retrieve(db_conn, sample_factory):
    """Tags inserted via upsert_sample are retrievable and ordered."""
    sample_factory(
        db_conn,
        name="Tagged Sample",
        category="Kick",
        path="/t/tagged.wav",
        tags=["alpha", "beta", "gamma"],
    )

    result = get_sample_by_path(db_conn, "/t/tagged.wav")
    assert result is not None
    assert set(result["tags"]) == {"alpha", "beta", "gamma"}

    # Tags are searchable via FTS
    results = search_samples(db_conn, "gamma")
    assert len(results) >= 1


# ---------------------------------------------------------------------------
# Analysis cache
# ---------------------------------------------------------------------------

def test_upsert_analysis(db_conn, sample_factory):
    """upsert_analysis stores analysis data linked to a sample."""
    sid = sample_factory(db_conn, name="Analyzed Kick", category="Kick", path="/a/kick.wav")

    upsert_analysis(db_conn, sid, {
        "bpm": 128.0,
        "key": "Fm",
        "pitch": "F",
        "note_number": 41,
        "is_atonal": False,
        "duration": 1.2,
        "sample_type": "oneshot",
        "spectral_centroid": 850.5,
    })

    row = db_conn.execute(
        "SELECT * FROM analysis_cache WHERE sample_id = ?", (sid,)
    ).fetchone()
    assert row is not None
    assert row["bpm"] == 128.0
    assert row["key"] == "Fm"
    assert row["pitch"] == "F"
    assert row["sample_type"] == "oneshot"
    assert row["is_atonal"] == 0


# ---------------------------------------------------------------------------
# Enriched search + enrich_result
# ---------------------------------------------------------------------------

def test_search_enriched(db_conn, sample_factory):
    """search_samples_enriched joins analysis data into results."""
    sid = sample_factory(
        db_conn, name="Enriched Kick", category="Kick", path="/e/kick.wav",
        tags=["punchy"],
    )
    upsert_analysis(db_conn, sid, {
        "bpm": 140.0,
        "key": "Am",
        "pitch": "A",
        "note_number": 45,
        "is_atonal": False,
        "duration": 1.0,
        "sample_type": "oneshot",
    })

    results = search_samples_enriched(db_conn, "kick")
    assert len(results) >= 1
    enriched = next(r for r in results if r["id"] == sid)
    assert enriched["bpm"] == 140.0
    assert enriched["key"] == "Am"
    assert enriched["sample_type"] == "oneshot"
    assert enriched["recommended_use"] == "drum_foundation"
    assert "confidence" in enriched


def test_enrich_result():
    """enrich_result computes confidence, recommended_use, ableton_action."""
    sample = {"id": 1, "category": "Kick", "name": "Boom Kick"}
    analysis = {
        "bpm": 128.0,
        "key": "Fm",
        "pitch": "F",
        "note_number": 41,
        "is_atonal": False,
        "duration": 1.5,
        "sample_type": "oneshot",
        "spectral_centroid": 900.0,
    }
    enriched = enrich_result(sample, analysis)

    # All analysis fields propagated
    assert enriched["bpm"] == 128.0
    assert enriched["key"] == "Fm"
    assert enriched["pitch"] == "F"
    assert enriched["sample_type"] == "oneshot"
    assert enriched["is_atonal"] is False

    # Confidence: key(0.4) + bpm(0.3) + pitch(0.2) + centroid(0.1) = 1.0
    assert enriched["confidence"] == 1.0

    # Recommended use from category map
    assert enriched["recommended_use"] == "drum_foundation"

    # Ableton action for Kick should reference pad 36
    assert "pad_index=36" in enriched["ableton_action"]

    # Compatible keys should include Fm's neighbours
    assert "Fm" in enriched["compatible_keys"]


def test_enrich_result_no_analysis():
    """enrich_result without analysis still returns sensible defaults."""
    sample = {"id": 2, "category": "FX", "name": "Riser"}
    enriched = enrich_result(sample, None)

    assert enriched["confidence"] == 0.0
    assert enriched["recommended_use"] == "transition"
    assert enriched["compatible_keys"] == []
    assert enriched["is_atonal"] is False


# ---------------------------------------------------------------------------
# Duplicate / similarity detection
# ---------------------------------------------------------------------------

def test_find_duplicates_by_hash(db_conn, sample_factory):
    """Two samples with the same file_hash are flagged as duplicates."""
    sample_factory(
        db_conn, name="Kick A", category="Kick",
        path="/d/kick_a.wav", file_hash="abc123",
    )
    sample_factory(
        db_conn, name="Kick B", category="Kick",
        path="/d/kick_b.wav", file_hash="abc123",
    )
    # Unique sample that should not appear
    sample_factory(
        db_conn, name="Unique", category="Snare",
        path="/d/unique.wav", file_hash="xyz789",
    )

    groups = find_duplicates_by_hash(db_conn)
    assert len(groups) == 1
    assert groups[0]["hash"] == "abc123"
    assert groups[0]["count"] == 2


def test_compute_file_hash_path_independent(tmp_path: Path):
    """compute_file_hash must NOT mix the file path into the hash.

    Two byte-identical files at different paths must produce the same hash
    so that find_duplicates_by_hash can detect them. (Bug I1: previously
    the path was part of the MD5 input, making dedup impossible.)
    """
    from librarian.db import compute_file_hash

    # Two identical files at different paths (same content, size, mtime)
    data = b"\x01\x02\x03\x04" * 1000
    f1 = tmp_path / "kick_original.wav"
    f2 = tmp_path / "copy" / "kick_copy.wav"
    (tmp_path / "copy").mkdir()
    f1.write_bytes(data)
    f2.write_bytes(data)
    # Equalize mtime so the content+size+mtime signal is identical
    import os
    os.utime(f1, (1000, 1000))
    os.utime(f2, (1000, 1000))

    h1 = compute_file_hash(str(f1))
    h2 = compute_file_hash(str(f2))
    assert h1 is not None
    assert h2 is not None
    assert h1 == h2, "path-independent hash should match for identical files"


def test_find_similar_by_duration(db_conn, sample_factory):
    """Samples with near-identical durations in the same category group together."""
    sid1 = sample_factory(
        db_conn, name="Kick 1", category="Kick", path="/du/k1.wav",
    )
    sid2 = sample_factory(
        db_conn, name="Kick 2", category="Kick", path="/du/k2.wav",
    )
    sid3 = sample_factory(
        db_conn, name="Snare 1", category="Snare", path="/du/s1.wav",
    )

    # Two kicks at almost the same duration, snare far apart
    for sid, dur in [(sid1, 1.00), (sid2, 1.02), (sid3, 3.00)]:
        upsert_analysis(db_conn, sid, {"duration": dur, "sample_type": "oneshot"})

    groups = find_similar_by_duration(db_conn, tolerance=0.1)
    # At least one group with ≥2 kicks
    kick_groups = [g for g in groups if g.get("category") == "Kick"]
    assert len(kick_groups) >= 1
    assert kick_groups[0]["count"] >= 2


def test_find_similar_by_pitch(db_conn, sample_factory):
    """Samples with same note_number + category group together."""
    sid1 = sample_factory(db_conn, name="Kick A", category="Kick", path="/p/ka.wav")
    sid2 = sample_factory(db_conn, name="Kick B", category="Kick", path="/p/kb.wav")
    sid3 = sample_factory(db_conn, name="Kick C", category="Kick", path="/p/kc.wav")

    # Two kicks at same pitch, one different
    upsert_analysis(db_conn, sid1, {"note_number": 41, "pitch": "F", "is_atonal": False, "sample_type": "oneshot"})
    upsert_analysis(db_conn, sid2, {"note_number": 41, "pitch": "F", "is_atonal": False, "sample_type": "oneshot"})
    upsert_analysis(db_conn, sid3, {"note_number": 48, "pitch": "C", "is_atonal": False, "sample_type": "oneshot"})

    groups = find_similar_by_pitch(db_conn, same_category=True)
    # The two 41-pitch kicks should form a group
    match = [g for g in groups if g.get("note_number") == 41]
    assert len(match) == 1
    assert match[0]["count"] == 2


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def test_get_stats(db_conn, sample_factory):
    """get_stats returns correct counts and category breakdown."""
    sample_factory(db_conn, name="K1", category="Kick", ext="wav", path="/st/k1.wav")
    sample_factory(db_conn, name="K2", category="Kick", ext="wav", path="/st/k2.wav")
    sample_factory(db_conn, name="S1", category="Snare", ext="aiff", path="/st/s1.wav")

    stats = get_stats(db_conn)
    assert stats["total_samples"] == 3
    assert stats["by_category"]["Kick"] == 2
    assert stats["by_category"]["Snare"] == 1
    assert stats["by_ext"]["wav"] == 2
    assert stats["by_ext"]["aiff"] == 1
    assert stats["analyzed_count"] == 0


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def test_migrate_from_jsonl(db_conn, tmp_path: Path):
    """migrate_from_jsonl loads records from a JSONL file."""
    records = [
        {"path": "/m/kick.wav", "name": "Migrated Kick", "category": "Kick", "ext": "wav"},
        {"path": "/m/snare.wav", "name": "Migrated Snare", "category": "Snare", "ext": "wav"},
    ]
    jsonl_path = tmp_path / "migrate.jsonl"
    jsonl_path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
    )

    count = migrate_from_jsonl(db_conn, str(jsonl_path))
    assert count == 2

    result = get_sample_by_path(db_conn, "/m/kick.wav")
    assert result is not None
    assert result["name"] == "Migrated Kick"
    assert result["category"] == "Kick"


def test_migrate_from_jsonl_missing_file(db_conn, tmp_path: Path):
    """migrate_from_jsonl returns 0 for a non-existent file."""
    count = migrate_from_jsonl(db_conn, str(tmp_path / "nonexistent.jsonl"))
    assert count == 0


# ---------------------------------------------------------------------------
# scan_root_to_db
# ---------------------------------------------------------------------------

def test_scan_root_to_db(tmp_path: Path):
    """scan_root_to_db scans a folder and upserts samples into the DB."""
    from librarian.db import get_db, get_stats, init_db, scan_root_to_db, search_samples

    # Create a fake sample folder
    root = tmp_path / "samples"
    root.mkdir()
    (root / "Kick").mkdir()
    (root / "Kick" / "808 Boom.wav").write_bytes(b"\x00" * 1024)
    (root / "Kick" / "Punchy Kick.wav").write_bytes(b"\x00" * 2048)
    (root / "Snare").mkdir()
    (root / "Snare" / "Clap.wav").write_bytes(b"\x00" * 512)

    db_path = str(tmp_path / "scan_test.db")
    init_db(db_path)
    conn = get_db(db_path)
    try:
        result = scan_root_to_db(conn, root)

        # All 3 audio files found
        assert result["files_found"] == 3
        assert result["files_new"] == 3
        assert result["root"] == str(root)

        # Samples are in the DB and searchable
        stats = get_stats(conn)
        assert stats["total_samples"] == 3

        hits = search_samples(conn, "boom")
        assert len(hits) == 1
        assert "808 Boom" in hits[0]["name"]

        # Re-scan: all updates (no new)
        result2 = scan_root_to_db(conn, root)
        assert result2["files_found"] == 3
        assert result2["files_new"] == 0
        assert result2["files_updated"] == 3
    finally:
        conn.close()


def test_scan_root_to_db_missing_root(tmp_path: Path):
    """scan_root_to_db returns zeros for a non-existent root."""
    from librarian.db import get_db, init_db, scan_root_to_db

    db_path = str(tmp_path / "missing_test.db")
    init_db(db_path)
    conn = get_db(db_path)
    try:
        result = scan_root_to_db(conn, tmp_path / "does_not_exist")
        assert result["files_found"] == 0
        assert result["files_new"] == 0
        assert result["files_updated"] == 0
    finally:
        conn.close()


def test_scan_root_to_db_empty_folder(tmp_path: Path):
    """scan_root_to_db on an empty folder finds nothing but still records a scan."""
    from librarian.db import get_db, get_stats, init_db, scan_root_to_db

    root = tmp_path / "empty"
    root.mkdir()

    db_path = str(tmp_path / "empty_test.db")
    init_db(db_path)
    conn = get_db(db_path)
    try:
        result = scan_root_to_db(conn, root)
        assert result["files_found"] == 0

        stats = get_stats(conn)
        assert stats["total_samples"] == 0

        # scan_history should still record the (empty) scan
        history = conn.execute(
            "SELECT files_found, status FROM scan_history WHERE root_path = ?",
            (str(root),),
        ).fetchone()
        assert history is not None
        assert history["files_found"] == 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# recommend_samples_db
# ---------------------------------------------------------------------------

def test_recommend_samples_db(db_conn, sample_factory):
    """recommend_samples_db returns harmonically compatible samples."""
    from librarian.db import recommend_samples_db, upsert_analysis

    # target_key "Fm" → compatible: ["Ab", "Fm", "A#m", "Cm"]
    # DB stores major-form keys; "Ab" is the major-form key in Fm's set.

    # Compatible: key "Ab" (in Fm's compatible set)
    sid_compatible = sample_factory(
        db_conn, name="Ab Bass", category="Bass", path="/r/bass_ab.wav",
    )
    upsert_analysis(db_conn, sid_compatible, {
        "key": "Ab", "pitch": "Ab", "note_number": 44,
        "is_atonal": False, "sample_type": "loop",
    })

    # Incompatible: key "E" (NOT in Fm's compatible set)
    sid_incompatible = sample_factory(
        db_conn, name="E Bass", category="Bass", path="/r/bass_e.wav",
    )
    upsert_analysis(db_conn, sid_incompatible, {
        "key": "E", "pitch": "E", "note_number": 40,
        "is_atonal": False, "sample_type": "loop",
    })

    # Atonal: always included regardless of key
    sid_atonal = sample_factory(
        db_conn, name="Closed Hat", category="HiHat", path="/r/hat.wav",
    )
    upsert_analysis(db_conn, sid_atonal, {
        "is_atonal": True, "sample_type": "oneshot",
    })

    results = recommend_samples_db(
        db_conn, target_key="Fm", terms=["bass", "hat"], limit=20,
    )
    names = [r["name"] for r in results]

    # Compatible bass included, incompatible bass excluded, atonal hat included
    assert "Ab Bass" in names
    assert "E Bass" not in names
    assert "Closed Hat" in names


def test_recommend_samples_db_no_terms(db_conn, sample_factory):
    """recommend_samples_db with no terms falls back to recent samples."""
    from librarian.db import recommend_samples_db, upsert_analysis

    # Compatible + category filter (no terms)
    sid = sample_factory(
        db_conn, name="Ab Bass", category="Bass", path="/r2/bass_ab.wav",
    )
    upsert_analysis(db_conn, sid, {
        "key": "Ab", "is_atonal": False, "sample_type": "loop",
    })
    # Incompatible, same category
    sid2 = sample_factory(
        db_conn, name="E Bass", category="Bass", path="/r2/bass_e.wav",
    )
    upsert_analysis(db_conn, sid2, {
        "key": "E", "is_atonal": False, "sample_type": "loop",
    })

    # No terms, category=Bass → should still filter by key compatibility
    results = recommend_samples_db(
        db_conn, target_key="Fm", terms=None, category="Bass", limit=20,
    )
    names = [r["name"] for r in results]
    assert "Ab Bass" in names
    assert "E Bass" not in names


def test_recommend_samples_db_no_analysis(db_conn, sample_factory):
    """Samples without analysis data are excluded from recommendations."""
    from librarian.db import recommend_samples_db

    # Sample with no analysis_cache row
    sample_factory(
        db_conn, name="Unknown Bass", category="Bass", path="/r3/bass.wav",
    )

    results = recommend_samples_db(
        db_conn, target_key="Fm", terms=["bass"], limit=20,
    )
    names = [r["name"] for r in results]
    assert "Unknown Bass" not in names
    assert results == []


def test_recommend_samples_db_no_candidates(db_conn, sample_factory):
    """No matching candidates returns an empty list."""
    from librarian.db import recommend_samples_db

    results = recommend_samples_db(
        db_conn, target_key="Fm", terms=["nonexistent_term_xyz"], limit=20,
    )
    assert results == []


def test_recommend_samples_db_sharp_key_normalization(db_conn, sample_factory):
    """シャープ表記のキー（librosa出力）もCamelot互換判定される。

    librosa は C#, D#, F#, G#, A#（シャープ）で格納するが、
    CAMELOT_WHEEL は Db, Eb, Gb, Ab, Bb（フラット）を使う。
    異名同音正規化なしでは39%の調性サンプルがレコメンドされない。
    """
    from librarian.db import recommend_samples_db, upsert_analysis

    # target_key "B" → compatible: ["B", "E", "A", "Gbm"(=F#m), "Dbm"(=C#m)]
    # つまり "A"（ナチュラル）は B の互換セットに含まれる。

    # シャープ表記のサンプル: key "C#"
    # → Camelot では Db と同じ音 → Db が target 互換セットに含まれれば推薦されるべき
    # target_key="Db" → compatible: ["Db", "Ab", "Gb", "Bbm"(=A#m), "Fm"]
    # "Db" 自身が含まれるので C#(=Db) サンプルは推薦されるべき
    sid_sharp = sample_factory(
        db_conn, name="C Sharp Lead", category="Synth",
        path="/r4/lead_csharp.wav",
    )
    upsert_analysis(db_conn, sid_sharp, {
        "key": "C#", "pitch": "C#", "note_number": 25,
        "is_atonal": False, "sample_type": "loop",
    })

    results = recommend_samples_db(
        db_conn, target_key="Db", terms=["lead"], limit=20,
    )
    names = [r["name"] for r in results]
    assert "C Sharp Lead" in names, (
        "シャープ表記 C# は Db と異名同音。Db の互換セットに Db が含まれるため"
        "推薦されるべきだが、正規化なしでは見逃される（バグ C1）"
    )
