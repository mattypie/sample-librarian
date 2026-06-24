# MCPサーバーのSQLite化設計

**日付**: 2026-06-24
**ステータス**: 承認済み（実装待ち）
**アプローチ**: A — `db.py` に不足関数を追加し、MCPは薄いラッパー化

## 背景・問題

`librarian/db.py` と `batch_analyze_sqlite.py` は SQLite + FTS5 バックエンドを完成させ、
実データ `data/samples.db` には 35,930 サンプル / 35,930 解析済みレコードが格納されている。

しかし **MCPサーバー（AIエージェントが使うメインAPI）はまだ旧JSONL方式のままで、
SQLite を見ていない。** 具体的には:

- `librarian_search` / `librarian_recommend` / `librarian_list_roots` が `_get_index_path()`
  （= `samples_index.jsonl`、実ファイルは0バイト）を読んでいるため、実質何も返さない。
- `librarian_index` / `librarian_add_root` は JSONL と summary.json を書き出す旧方式。

加えて、既存の `db.search_samples()` には **FTS5の0件バグ** がある:
各トークンをダブルクォートで暗黙AND結合しているため、複数語を指定すると
「全語を含むサンプル」が実質なくなり、結果が空になる。FTS自体は正常
（`kick` 単体で3,022件ヒット）。

## 目標

1. MCPの該当5ツールを SQLite (`db.py`) 経由に移行し、JSONL依存を排除する。
2. FTS5検索の0件バグを修正する。
3. MCP層は薄いラッパー（ツール定義・引数・JSON変換のみ）に徹し、
   ビジネスロジックは `db.py` に集約する。

## 非目標（今回やらないこと）

- `librarian/search.py` / `recommend.py` / `index.py` のJSONLコード削除
  （ファイルは残す。MCPからの参照を切るだけ。別タスクで整理）
- `batch_analyze_sqlite.py` のリファクタリング
- SQLiteと無関係なMCPツール（`analyze` / `analyze_folder` / `preview` / `load_to_pad`）の変更

## アーキテクチャ

```
mcp_server.py  (薄いラッパー: ツール定義・引数バリデーション・JSON変換のみ)
        │
        ▼
librarian/db.py  (ビジネスロジック: 検索・スキャン・レコメンド)
        │
        ├── librarian/index.py  (既存の _scan_folder / _infer_category / _rough_tags / _extract_strings を再利用)
        ├── librarian/analyze.py (既存の get_compatible_keys を再利用)
        ▼
data/samples.db  (SQLite + FTS5)
```

## 詳細設計

### 1. FTS5検索の修正（`db.search_samples`）

**現状**（行539）:
```python
fts_query = " ".join('"' + t.replace('"', '""') + '"' for t in tokens)
# → "808" "kick" "punchy"  (暗黙AND → 全語必須 → ほぼ0件)
```

**修正方針**: AND優先・ORフォールバックの2段階検索。

1. まずAND（精密）クエリを投げる: `"808" "kick" "punchy"`
2. 結果が0件なら、OR（緩和）クエリに切替: `"808" OR "kick" OR "punchy"`
3. どちらもBM25でランキングされるため、OR時も「より多くのトークンを含む」
   サンプルが上位に来る。
4. `category` フィルタは両段階で適用（既存の `lower(s.category) = lower(?)`）。

実装は `_run_fts(conn, fts_query, category, limit)` という内部ヘルパに抽出し、
AND→ORのフォールバックだけを `search_samples` で制御する。
`search_samples_enriched` は `search_samples` を呼ぶ上位関数なので、
修正は自動的に伝播する。

**テスト**: 実DBで以下が0件でなくなることを確認。
- `search_samples(conn, '808 kick punchy', category='Kick')`
- `search_samples(conn, 'snare punchy')`

### 2. `db.py` 新関数: `scan_root_to_db`

```python
def scan_root_to_db(
    conn: sqlite3.Connection,
    root_path: str | Path,
    scan_presets: bool = True,
) -> dict[str, int | str]:
    """フォルダをスキャンして samples テーブルへ upsert する。

    librarian/index.py の _scan_folder / _infer_category / _rough_tags /
    _extract_strings を再利用してレコードを組み立て、file_hash を計算して
    upsert_sample() で永続化する。librosa 分析は行わない（メタデータのみ）。
    """
```

- `index.py` から `_scan_folder` / `_infer_category`（公開APIではないが実績あり）を
  import して再利用。ロジックの重複を避ける。
- 各レコードに対し `file_hash` を計算し、既存の `upsert_sample()` で書き込み。
- `record_scan()` と `update_root()` も呼び、スキャン履歴と roots テーブルを更新。
- **librosa 分析は行わない** — 分析は別工程（`batch_analyze_sqlite.py` /
  `reanalyze_errors.py` / `librarian_analyze`）の責務。

戻り値:
```python
{"files_found": 812, "files_new": 812, "files_updated": 0, "root": str(root)}
```

### 3. `db.py` 新関数: `recommend_samples_db`

```python
def recommend_samples_db(
    conn: sqlite3.Connection,
    target_key: str,
    terms: list[str] | None = None,
    category: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """analysis_cache の事前解析データと Camelot 互換性でレコメンド。

    旧 recommend.py の「JSONL全体読込＋毎回 analyze_file()」を廃止し、
    analysis_cache に保存済みの key/sample_type を使って協調性フィルタする。
    """
```

処理フロー:
1. `get_compatible_keys(target_key)` で互換キー群を取得。
2. FTS5検索（`search_samples`）で候補を抽出（terms/category フィルタ）。
3. 候補を `analysis_cache` と JOIN して key / is_atonal を取得。
4. **無調性判定は `is_atonal` 列を使う**（0/1）。`is_atonal=1` のサンプルは
   キー制約なしで常に候補として残す（ハット/ノイズ/FX 等は調性がないため）。
   ※ `sample_type` 列は oneshot/short_loop/medium_loop/long_loop の長さ分類であり、
   調性とは無関係なのでレコメンド判定には使わない。
5. `is_atonal=0` のサンプルは、key が互換キー群に含まれる場合のみ残す。
6. 互換するものを優先的にソートして limit 件返す。

**リアルタイム分析はしない**。analysis_cache に key がないサンプルは
推奨不能として除外する（分析済みのサンプルが 35,930 件あるため実用上問題ない）。

**実データの制約（重要）**: `analysis_cache.key` は `"C"`, `"C#"`, `"G"` のように
音名のみで格納されており、メジャー/マイナーの区別がない。一方
`get_compatible_keys()` は `"Fm"`（マイナー）のような入力も受け付ける。
実装では `target_key` をそのまま `get_compatible_keys()` に渡し、
戻り値の互換キー群と `analysis_cache.key` を比較する。音名のみで
比較するため、マイナー指定時の精度は落ちるが、現状のデータ
（major-form の key のみ）では妥当な挙動となる。将来 key 検出が
major/minor 区別するようになれば自動的に精度が向上する。

### 4. MCPツール書き換え（`mcp_server.py`）

各ツールの docstring・引数名・戻り値のJSON構造は **現状を維持** する
（呼び出し側のAIエージェントへの互換性を保つ）。

| ツール | 変更内容 |
|---|---|
| `librarian_search` | `_get_index_path()` 削除 → `get_db()` で接続し `search_samples_enriched()` を呼ぶ |
| `librarian_index` | `build_index()` 削除 → 各 root に対し `scan_root_to_db()` を呼ぶ。JSONL/summary 書き出し削除 |
| `librarian_add_root` | 設定に root 追加後、即座に `scan_root_to_db()` でスキャン |
| `librarian_list_roots` | `get_stats()` から roots 情報 + サンプル数を返す |
| `librarian_recommend` | `recommend_samples()` 削除 → `recommend_samples_db()` を呼ぶ。`load_records()` / `analyze_file()` 削除 |

DB接続は各ツール呼び出し毎に `get_db()` で取得し、`try/finally` で close する。
（長寿命接続は MCP の非同期ライフサイクルと相性が悪いため、1リクエスト1接続）

## テスト方針

1. **FTS修正の検証**（最優先）:
   - `search_samples(conn, '808 kick punchy', category='Kick')` → 0件でない
   - `search_samples(conn, 'snare punchy')` → 0件でない
   - 単語クエリ（`kick`）が従来通りヒットすることの回帰確認

2. **スキャン**:
   - 小さなテストフォルダで `scan_root_to_db()` が samples を正しく upsert
   - 同じフォルダの再スキャンで `files_updated` が適切にカウントされる

3. **レコメンド**:
   - `recommend_samples_db(conn, "Fm", terms=["bass"])` が互換キーのサンプルを返す
   - analysis_cache に key がないサンプルが除外される

4. **MCP統合**:
   - `mcp_server.py` を起動し、各ツールのレスポンスを手動確認

## リスクと緩和

- **リスク**: `index.py` の非公開関数（`_scan_folder` 等）への依存。
  - **緩和**: import は失敗しない（同一パッケージ内）。将来のリファクタで
    壊れたら、その時スキャンロジックを `db.py` 内に持てばよい。
- **リスク**: ORフォールバックで検索結果がノイジーになりすぎる。
  - **緩和**: BM25ランキングが「全語含む」サンプルを上位に置くため、
    limit で抑えれば実用上問題ない。
- **リスク**: `recommend_samples_db` で key 未解析サンプルが除外され、結果が少ない。
  - **緩和**: 35,930 件すべて解析済みのため、現状のデータでは発生しない。

## 影響範囲

- **変更**: `librarian/db.py`（FTS修正 + 新関数2つ）、`mcp_server.py`（5ツール）
- **新規作成**: なし
- **削除**: なし（JSONLコードは残す。参照を切るだけ）
- **互換性**: MCPの外部API（ツール名・引数・戻り値構造）は維持
