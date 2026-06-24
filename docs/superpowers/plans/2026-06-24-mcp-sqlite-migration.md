# MCPサーバー SQLite化 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** MCPサーバーの5ツール（search / index / add_root / list_roots / recommend）を旧JSONL方式から SQLite (`db.py`) バックエンドに移行し、FTS5検索の0件バグを修正する。

**Architecture:** ビジネスロジックを `librarian/db.py` に集約し、`mcp_server.py` は薄いラッパー（ツール定義・引数バリデーション・JSON変換のみ）に徹する。FTS5検索は AND優先・ORフォールバック の2段階に修正。新関数 `scan_root_to_db` / `recommend_samples_db` を `db.py` に追加。

**Tech Stack:** Python 3.11+, SQLite3 + FTS5 (標準ライブラリ), pytest, MCP (FastMCP)

**Spec:** `docs/superpowers/specs/2026-06-24-mcp-sqlite-migration-design.md`

---

## ファイル構成

- **修正**: `librarian/db.py` — `search_samples` のFTS修正 + `_run_fts` ヘルパ抽出 + `scan_root_to_db` / `recommend_samples_db` 新関数
- **修正**: `mcp_server.py` — 5ツールを `db.py` 経由に書き換え、`_get_index_path()` を `_get_db()` に置換、旧import削除
- **修正**: `tests/test_db.py` — FTS修正・新関数のテストを追加
- **新規作成**: なし
- **削除**: なし（`search.py` / `recommend.py` / `index.py` はファイルとして残す。参照を切るだけ）

## 実装順序の根拠

1. **Task 1**（FTS修正）は他のすべての作業の前提。検索が動かないとレコメンドも統合テストも検証不能。
2. **Task 2-3**（`db.py` 新関数）は純粋な関数として単体テスト可能。MCPに依存しない。
3. **Task 4**（MCP書き換え）は Task 1-3 の関数を使うので最後。
4. **Task 5**（統合テスト・lint）で全体を検証。

---

## Task 1: FTS5検索の0件バグ修正（AND優先・ORフォールバック）

**Files:**
- Modify: `librarian/db.py:503-563`（`search_samples`）
- Test: `tests/test_db.py`（既存 `test_fts5_search_multiword` の後に追加）

**背景**: 現状 `search_samples` は各トークンを `"808" "kick" "punchy"` と暗黙AND結合する（`db.py:539`）。3語すべてを含むサンプルが実質なく0件になる。修正は AND優先・ORフォールバック の2段階検索。

- [ ] **Step 1: 失敗テストを書く（OR フォールバックの検証）**

`tests/test_db.py` の `test_fts5_search_multiword` の直後に追加する。

```python
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
    # ORフォールバックで各サンプルが1語ずつマッチするはず
    results = search_samples(db_conn, "808 snare")
    assert len(results) >= 1
    names = [r["name"] for r in results]
    assert "808 Kick" in names or "Punchy Snare" in names
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd "/Volumes/dock st 1TB/Dev/sample-librarian" && .venv/bin/python3 -m pytest tests/test_db.py::test_fts5_search_or_fallback -v`
Expected: FAIL（現状はAND結合で0件になるため `assert len(results) >= 1` が失敗）

- [ ] **Step 3: `_run_fts` ヘルパと `search_samples` を実装**

`librarian/db.py:503` の `search_samples` 関数全体を以下に置き換える。`_run_fts` は同ファイル内の `search_samples` の**直前**（`get_sample_tags` の後、`get_sample_by_path` の前あたり、つまり `search_samples` の上）に追加する。

まず `search_samples` の上（`# Public API — queries` セクションヘッダの直後）に `_run_fts` を追加:

```python
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
```

次に `search_samples` を以下に置き換え:

```python
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
        return []  # single token already tried in AND stage
    fts_or = " OR ".join(_quote(t) for t in tokens)
    return _run_fts(conn, fts_or, category, limit)
```

- [ ] **Step 4: 新テスト + 既存テストが通ることを確認**

Run: `cd "/Volumes/dock st 1TB/Dev/sample-librarian" && .venv/bin/python3 -m pytest tests/test_db.py -v -k "fts5 or category"`
Expected: `test_fts5_search`, `test_fts5_search_multiword`, `test_fts5_search_or_fallback`, `test_category_filter` すべて PASS

- [ ] **Step 5: 実DBで0件バグが解消したことを確認**

Run: `cd "/Volumes/dock st 1TB/Dev/sample-librarian" && .venv/bin/python3 -c "
from librarian.db import get_db, search_samples_enriched
conn = get_db('data/samples.db')
r1 = search_samples_enriched(conn, '808 kick punchy', category='Kick', limit=3)
r2 = search_samples_enriched(conn, 'snare punchy', limit=3)
print('808 kick punchy (Kick):', len(r1), [x['name'] for x in r1])
print('snare punchy:', len(r2), [x['name'] for x in r2])
conn.close()
"`
Expected: 両方とも0件ではなく、実際のサンプル名が出力される

- [ ] **Step 6: コミット**

```bash
cd "/Volumes/dock st 1TB/Dev/sample-librarian"
git add librarian/db.py tests/test_db.py
git commit -m "fix: FTS5 search AND→OR fallback for multi-word queries

Multi-word queries were implicitly AND-joined, returning 0 results when
no sample contained all tokens. Now falls back to OR when AND yields
nothing, ranked by BM25."
```

---

## Task 2: `scan_root_to_db` — フォルダスキャン→SQLite書き込み

**Files:**
- Modify: `librarian/db.py`（`recommend_samples_db` は Task 3、ここでは `scan_root_to_db` のみ）
- Test: `tests/test_db.py`

**背景**: `index.py:_scan_folder`（`db.py` と同じパッケージ）が `path/name/ext/size/category/folder/root/tags/strings` を持つ record を生成する。これに `file_hash` を付与して `upsert_sample` で書き込む。`file_hash` の計算ロジックは `batch_analyze_sqlite.py:40` の実績ある実装（`md5(path:size)`）を踏襲。librosa分析は行わない。

- [ ] **Step 1: 失敗テストを書く**

`tests/test_db.py` に追加。テスト用のダミーオーディオファイルを `tmp_path` に作り、スキャン結果を検証する。

```python
def test_scan_root_to_db(tmp_path: Path):
    """scan_root_to_db scans a folder and upserts samples into the DB."""
    from librarian.db import get_db, init_db, scan_root_to_db, search_samples, get_stats

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
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd "/Volumes/dock st 1TB/Dev/sample-librarian" && .venv/bin/python3 -m pytest tests/test_db.py::test_scan_root_to_db -v`
Expected: FAIL（`ImportError: cannot import name 'scan_root_to_db'`）

- [ ] **Step 3: `scan_root_to_db` を実装**

`librarian/db.py` の `update_root` 関数（行621-634）の**直後**に追加。`_scan_folder` は `librarian.index` から import する（モジュール上部の import には追加せず、関数内で遅延 import して循環参照リスクを避ける）。

```python
def _compute_file_hash(path: str) -> str | None:
    """Quick hash of file path + size for dedup (matches batch_analyze_sqlite)."""
    import os
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
        record["file_hash"] = _compute_file_hash(record["path"])
        upsert_sample(conn, record)
        if record["path"] in existing:
            files_updated += 1
        else:
            files_new += 1

    record_scan(conn, str(root), found, files_new, files_updated)
    update_root(conn, str(root), found)

    return {"files_found": found, "files_new": files_new,
            "files_updated": files_updated, "root": str(root)}
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd "/Volumes/dock st 1TB/Dev/sample-librarian" && .venv/bin/python3 -m pytest tests/test_db.py::test_scan_root_to_db -v`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
cd "/Volumes/dock st 1TB/Dev/sample-librarian"
git add librarian/db.py tests/test_db.py
git commit -m "feat: add scan_root_to_db for folder→SQLite ingestion

Reuses index._scan_folder to build records, adds file_hash, and upserts
into samples table. Updates roots + scan_history. No librosa analysis."
```

---

## Task 3: `recommend_samples_db` — analysis_cacheベースのレコメンド

**Files:**
- Modify: `librarian/db.py`（`scan_root_to_db` の直後に追加）
- Test: `tests/test_db.py`

**背景**: 旧 `recommend.py` はJSONL全行読込＋毎回 `analyze_file()`。新実装は `analysis_cache` の事前解析データ（key/is_atonal）と `get_compatible_keys()` を使う。リアルタイム分析なし。無調性判定は `is_atonal` 列（0/1）を使用。`sample_type` は長さ分類なので使わない。

**重要な制約**: `analysis_cache.key` は `"C"` `"C#"` `"G"` のように音名のみ（major/minor区別なし）。`get_compatible_keys("C")` は `["C", "G", "Db", "Am"]` を返す（minor含む）。DBのkeyはmajor-formのみなので、実際にマッチするのは戻り値のうちDBに存在する音名のみ。この挙動は仕様（spec参照）。

- [ ] **Step 1: 失敗テストを書く**

`tests/test_db.py` に追加。analysis_cache に key を設定したサンプルを用意し、target_key との互換性を検証。

```python
def test_recommend_samples_db(db_conn, sample_factory):
    """recommend_samples_db returns harmonically compatible samples."""
    from librarian.db import recommend_samples_db, upsert_analysis

    # target_key "Fm" → compatible: ["Fm","Dbm","Cm","Gm","Ab","Eb","Bb","..."]
    # Analysis_cache stores major-form keys ("C","G",...). Among get_compatible_keys("Fm"),
    # "Ab" and "Eb" are major-form keys that would be present.
    # is_atonal samples are always included regardless of key.

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

    # Atonal: always included
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
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd "/Volumes/dock st 1TB/Dev/sample-librarian" && .venv/bin/python3 -m pytest tests/test_db.py::test_recommend_samples_db -v`
Expected: FAIL（`ImportError: cannot import name 'recommend_samples_db'`）

- [ ] **Step 3: `recommend_samples_db` を実装**

`librarian/db.py` の `scan_root_to_db` の直後に追加。

```python
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
        Enriched sample dicts (via :func:`enrich_result`), compatible
        samples first.
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
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd "/Volumes/dock st 1TB/Dev/sample-librarian" && .venv/bin/python3 -m pytest tests/test_db.py::test_recommend_samples_db -v`
Expected: PASS

もし FAIL する場合、`get_compatible_keys("Fm")` の実際の戻り値を確認し、テストで使う互換/非互換キーを調整する:
`cd "/Volumes/dock st 1TB/Dev/sample-librarian" && .venv/bin/python3 -c "from librarian.analyze import get_compatible_keys; print(get_compatible_keys('Fm'))"`

- [ ] **Step 5: コミット**

```bash
cd "/Volumes/dock st 1TB/Dev/sample-librarian"
git add librarian/db.py tests/test_db.py
git commit -m "feat: add recommend_samples_db using analysis_cache + Camelot

Replaces real-time analyze_file() calls with pre-computed analysis_cache
lookup. Atonal samples always included; tonal filtered by compatible keys."
```

---

## Task 4: MCPサーバーの5ツールをSQLite化

**Files:**
- Modify: `mcp_server.py:36-66`（imports + `_get_index_path`）、`:77-101`（search）、`:104-124`（index）、`:127-222`（add_root）、`:225-277`（list_roots）、`:318-354`（recommend）

**背景**: `_get_index_path()` を `_get_db()` に置換。各ツールは `try/finally` でDB接続を閉じる。ツールの docstring・引数名・戻り値のJSON構造は現状維持（AIエージェント互換性）。`config.py:get_samples_roots()` は引き続き使う（roots設定はJSONL/SQLite関係ないため）。

- [ ] **Step 1: import と接続ヘルパを書き換え**

`mcp_server.py:36-66` の以下の部分を修正。

**旧（行36-39）**:
```python
from librarian.analyze import analyze_file, analyze_folder, get_compatible_keys
from librarian.index import IndexConfig, build_index
from librarian.recommend import recommend_samples
from librarian.search import search_index
```

**新**:
```python
from librarian.analyze import analyze_file, analyze_folder, get_compatible_keys
from librarian.db import get_db, scan_root_to_db, search_samples_enriched, recommend_samples_db
```

（`IndexConfig` / `build_index` / `recommend_samples` / `search_index` の import を削除）

**旧（行59-65）** `_get_index_path` 関数を、以下の `_get_db` に置き換え:

```python
def _get_db_path() -> str:
    """Get the SQLite database path from config or default."""
    try:
        from librarian.config import get_db_path
        return get_db_path()
    except Exception:
        return str(Path(__file__).parent / "data" / "samples.db")


def _get_db():
    """Open a DB connection for a single tool call. Caller must close()."""
    return get_db(_get_db_path())
```

**確認**: `config.py` には `get_db_path` が**まだ存在しない**（`get_index_path` / `get_summary_path` のみ）。`get_summary_path`（行58-64）の直後に以下を**必ず追加**する:

```python
def get_db_path() -> str:
    """SQLite database path (default: data/samples.db)."""
    return os.environ.get(
        "SAMPLE_LIBRARIAN_DB",
        str(_BASE_DIR / "data" / "samples.db"),
    )
```

`_BASE_DIR` は `config.py` の既存変数（行14-15で定義済み）なのでそのまま使える。

- [ ] **Step 2: `librarian_search` を書き換え**

`mcp_server.py:77-101` を以下に置き換え。docstring と引数は維持。

```python
@mcp.tool()
def librarian_search(
    terms: list[str],
    limit: int = 20,
    category: str = "",
    ext: str = "",
) -> str:
    """Search the sample index by keywords.

    Args:
        terms: Search terms (all must match, AND logic)
        limit: Max results (default 20)
        category: Filter by category (e.g., "Kick", "Snare")
        ext: Filter by extension (e.g., ".wav")

    Returns:
        JSON array of matching samples with name, path, category, tags.
    """
    query = " ".join(terms) if terms else ""
    conn = _get_db()
    try:
        results = search_samples_enriched(
            conn, query,
            category=category or None,
            limit=limit,
        )
        if ext:
            results = [r for r in results if r.get("ext", "").lstrip(".").lower()
                       == ext.lstrip(".").lower()]
    finally:
        conn.close()
    return json.dumps(results, ensure_ascii=False)
```

- [ ] **Step 3: `librarian_index` を書き換え**

`mcp_server.py:104-124` を以下に置き換え。

```python
@mcp.tool()
def librarian_index(
    roots: list[str],
    scan_presets: bool = True,
) -> str:
    """Build or rebuild the sample index from folder(s).

    Args:
        roots: List of root folders to scan
        scan_presets: Include preset files (.nmsv, .nksf, etc.)

    Returns:
        JSON summary with total files, categories, sizes.
    """
    from librarian.db import get_stats

    conn = _get_db()
    try:
        summaries = []
        total_new = 0
        for root in roots:
            r = scan_root_to_db(conn, root, scan_presets=scan_presets)
            summaries.append(r)
            total_new += r["files_new"]

        stats = get_stats(conn)
        summary = {
            "total_files": stats["total_samples"],
            "total_new": total_new,
            "roots_scanned": summaries,
            "categories": stats["by_category"],
            "analyzed_count": stats["analyzed_count"],
        }
    finally:
        conn.close()
    return json.dumps(summary, ensure_ascii=False)
```

- [ ] **Step 4: `librarian_add_root` を書き換え**

`mcp_server.py:127-222` を以下に置き換え。config.local.py の編集ロジック（行144-202）は**そのまま維持**し、再スキャン部分だけ `scan_root_to_db` に変える。

該当箇所は `if rebuild_index:` ブロック（行211-220）。以下に置き換え:

```python
    # Re-index if requested
    if rebuild_index:
        from librarian.db import get_stats

        all_roots = current_roots + [clean_path]
        conn = _get_db()
        try:
            scan_results = []
            for r in all_roots:
                scan_results.append(scan_root_to_db(conn, r, scan_presets=True))
            stats = get_stats(conn)
        finally:
            conn.close()

        result["index_summary"] = {
            "total_files": stats["total_samples"],
            "roots_scanned": scan_results,
            "categories": stats["by_category"],
        }
        result["total_roots"] = len(all_roots)
```

（`config.local.py` の編集ロジック `# Read or create config.local.py` 〜 `local_path.write_text(...)` は変更なし）

**注意**: `import os`（行144）は既存のまま維持。`add_root` の先頭の `import os` と `from config import get_samples_roots` はそのまま残す。

- [ ] **Step 5: `librarian_list_roots` を書き換え**

`mcp_server.py:225-277` を以下に置き換え。

```python
@mcp.tool()
def librarian_list_roots() -> str:
    """Show all configured sample folders and current index status.

    Returns:
        JSON with:
        - roots: list of configured folder paths with on-disk status
        - index: DB summary (total samples, analyzed count, last scan)
    """
    from librarian.db import get_stats

    from config import get_samples_roots
    roots = get_samples_roots()

    # On-disk status per root (unchanged logic)
    root_status = []
    for r in roots:
        expanded = os.path.expanduser(r)
        exists = os.path.isdir(expanded)
        file_count = 0
        if exists:
            for ext in (".wav", ".aiff", ".aif", ".mp3", ".ogg", ".flac"):
                file_count += sum(
                    1 for _ in Path(expanded).rglob(f"*{ext}")
                )
        root_status.append({
            "path": r,
            "exists": exists,
            "audio_files": file_count,
        })

    # DB stats instead of JSONL line count
    conn = _get_db()
    try:
        stats = get_stats(conn)
        # Last scan timestamp from scan_history
        last_scan_row = conn.execute(
            "SELECT completed_at FROM scan_history "
            "ORDER BY completed_at DESC LIMIT 1"
        ).fetchone()
        last_scan = last_scan_row[0] if last_scan_row else None
    finally:
        conn.close()

    return json.dumps({
        "roots": root_status,
        "index": {
            "exists": stats["total_samples"] > 0,
            "indexed_files": stats["total_samples"],
            "analyzed_count": stats["analyzed_count"],
            "last_built": last_scan,
            "by_category": stats["by_category"],
        },
    }, ensure_ascii=False)
```

- [ ] **Step 6: `librarian_recommend` を書き換え**

`mcp_server.py:318-354` を以下に置き換え。

```python
@mcp.tool()
def librarian_recommend(
    target_key: str,
    terms: list[str] | None = None,
    category: str = "",
    limit: int = 20,
    analyze: bool = False,
) -> str:
    """Recommend samples harmonically compatible with a target key.

    Uses Camelot Wheel matching (adjacent keys ±1, relative major/minor).

    Args:
        target_key: Target key (e.g., "Fm", "C", "Am")
        terms: Optional search terms to filter
        category: Filter by category
        limit: Max results
        analyze: Reserved (analysis comes from cache, not real-time)

    Returns:
        JSON array of recommended samples.
    """
    conn = _get_db()
    try:
        results = recommend_samples_db(
            conn,
            target_key=target_key,
            terms=terms,
            category=category or None,
            limit=limit,
        )
    finally:
        conn.close()
    compatible = get_compatible_keys(target_key)
    output = {
        "target_key": target_key,
        "compatible_keys": compatible,
        "results": results,
    }
    return json.dumps(output, ensure_ascii=False)
```

**注意**: `analyze: bool = False` パラメータは互換性のため**残す**が、docstringに「Reserved」と明記し、実装では無視する（analysis_cacheを使うため）。`analyze_folder` / `analyze_file` の import は別ツールで使うので残す。

- [ ] **Step 7: `mcp_server.py` が構文エラーなく import できることを確認**

Run: `cd "/Volumes/dock st 1TB/Dev/sample-librarian" && .venv/bin/python3 -c "import mcp_server; print('OK')" 2>&1 | tail -5`
Expected: `OK`（または `MCP package not installed` — この場合は `--no-verify` 等でimportだけ確認）

注意: `mcp` パッケージが未インストールの場合 `sys.exit(1)` される。その場合は `FastMCP` の import を除いて構文チェック:
Run: `cd "/Volumes/dock st 1TB/Dev/sample-librarian" && .venv/bin/python3 -m py_compile mcp_server.py && echo "COMPILE OK"`
Expected: `COMPILE OK`

- [ ] **Step 8: コミット**

```bash
cd "/Volumes/dock st 1TB/Dev/sample-librarian"
git add mcp_server.py config.py
git commit -m "feat: migrate MCP server tools to SQLite backend

Switches librarian_search/index/add_root/list_roots/recommend from
JSONL to db.py functions (search_samples_enriched, scan_root_to_db,
recommend_samples_db, get_stats). MCP layer is now a thin wrapper."
```

---

## Task 5: 統合検証・lint

**Files:** なし（検証のみ）

- [ ] **Step 1: 全テストスイートが通ることを確認**

Run: `cd "/Volumes/dock st 1TB/Dev/sample-librarian" && .venv/bin/python3 -m pytest tests/ -v`
Expected: 全テスト PASS（既存 + 新規）

- [ ] **Step 2: ruff lint**

Run: `cd "/Volumes/dock st 1TB/Dev/sample-librarian" && .venv/bin/ruff check librarian/db.py mcp_server.py config.py tests/test_db.py`
Expected: `All checks passed!`

修正が必要な場合:
- 未使用 import（`F401`）: 削除
- 行長すぎ（`E501`）: 適宜改行
- 並び順（`I001`）: `ruff check --fix` で自動修正

- [ ] **Step 3: 実DBでエンドツーエンド動作確認**

MCPサーバー経由ではなく、`db.py` の関数を直接叩いて実データで検証。

Run:
```bash
cd "/Volumes/dock st 1TB/Dev/sample-librarian" && .venv/bin/python3 -c "
import json
from librarian.db import get_db, search_samples_enriched, recommend_samples_db, get_stats

conn = get_db('data/samples.db')

# 1. Stats
stats = get_stats(conn)
print('=== STATS ===')
print(f'total_samples: {stats[\"total_samples\"]}')
print(f'analyzed_count: {stats[\"analyzed_count\"]}')
print(f'top categories: {dict(list(stats[\"by_category\"].items())[:5])}')

# 2. Search (was 0-result bug)
print('=== SEARCH: 808 kick punchy ===')
hits = search_samples_enriched(conn, '808 kick punchy', category='Kick', limit=3)
for h in hits:
    print(f'  - {h[\"name\"]} [{h[\"category\"]}] key={h.get(\"key\")}')
print(f'  ({len(hits)} results)')

# 3. Recommend
print('=== RECOMMEND: Fm, terms=[bass] ===')
recs = recommend_samples_db(conn, target_key='Fm', terms=['bass'], limit=5)
for r in recs:
    print(f'  - {r[\"name\"]} [{r[\"category\"]}] key={r.get(\"key\")} atonal={r.get(\"is_atonal\")}')
print(f'  ({len(recs)} results)')

conn.close()
print('=== ALL OK ===')
"
```
Expected:
- `total_samples: 35930`, `analyzed_count: 35930`
- SEARCH が0件ではなく実際のサンプル名を出力
- RECOMMEND が互換キーのベースサンプルを返す（0件でない）

- [ ] **Step 4: MCPツール関数を直接呼び出して確認**

`mcp` パッケージの有無にかかわらず、ツール関数の本体を呼べる。

Run:
```bash
cd "/Volumes/dock st 1TB/Dev/sample-librarian" && .venv/bin/python3 -c "
import json, mcp_server

# librarian_search
out = mcp_server.librarian_search(['kick', 'punchy'], limit=3)
results = json.loads(out)
print('search results:', len(results))
for r in results:
    print('  -', r.get('name'), '|', r.get('category'))

# librarian_list_roots
out = mcp_server.librarian_list_roots()
info = json.loads(out)
print('list_roots indexed_files:', info['index']['indexed_files'])
print('list_roots roots:', len(info['roots']))
" 2>&1 | tail -20
```
Expected:
- `mcp` パッケージがインストールされていれば search/list_roots の結果が出力される
- 未インストールなら `sys.exit(1)` で止まる（その場合は `python3 -m py_compile` で構文確認済みなのでスキップ可）

- [ ] **Step 5: 最終コミット（変更があれば）**

```bash
cd "/Volumes/dock st 1TB/Dev/sample-librarian"
git status
# lint 修正があれば:
git add -A
git commit -m "style: ruff fixes for SQLite migration"
```

- [ ] **Step 6: 完了確認**

Run: `cd "/Volumes/dock st 1TB/Dev/sample-librarian" && git log --oneline -6`
Expected: 本計画のコミットが5個（FTS fix, scan_root_to_db, recommend_samples_db, MCP migration, lint fixes）並ぶ。

---

## 完了条件

- [x] FTS5の多語検索で0件にならない（AND→ORフォールバック）
- [x] `scan_root_to_db` がフォルダをスキャンしてSQLiteに書き込む
- [x] `recommend_samples_db` がanalysis_cacheベースで互換サンプルを返す
- [x] MCPの5ツールがSQLite経由で動く（JSONL参照なし）
- [x] `pytest tests/` 全PASS
- [x] `ruff check` クリーン
- [x] 実DB（35,930サンプル）で検索・レコメンドが結果を返す
