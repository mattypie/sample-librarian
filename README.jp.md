<p align="center">
  <img src="assets/banner_jp.jpg" alt="Sample Librarian" width="640">
</p>

<p align="center">
  <a href="README.md">English</a> |
  <a href="README.jp.md">日本語</a> |
  <a href="README.zh.md">中文</a> |
  <a href="README.kr.md">한국어</a> |
  <a href="README.es.md">Español</a> |
  <a href="README.fr.md">Français</a>
</p>

# Sample Librarian

ローカルライブラリからオーディオサンプルの検索、分析、おすすめ提案を行います。
SQLite + FTS5フルテキスト検索、AIフレンドリーな結果エンリッチメント、
重複検出、Camelot Wheelハーモニックマッチングを備えています。単体でも動作し、
[live-agent-remote](https://github.com/happytown-s/live-agent-remote)
と連携してAbleton LiveでのプレビューやワンショットDrum Rack構築も可能です。

## 機能

- **ルート管理** — サンプルフォルダを設定に追加すると自動で再インデックス
- **インデックス** — フォルダをスキャンし、メタデータを抽出してSQLite + FTS5に格納
- **検索** — 名前、カテゴリ、タグ、フォルダパスを対象としたBM25フルテキスト検索
- **エンリッチされた結果** — 各検索結果に `key`、`bpm`、`pitch`、`confidence`、`recommended_use`、`ableton_action` を含む — AIエージェントがそのまま行動できる形式
- **分析** — librosaベースのピッチ検出、BPM、キー推定、スペクトル分析
- **重複検出** — コンテンツハッシュ、再生時間、ピッチ、スペクトルフィンガープリントの4軸で重複を検出
- **おすすめ提案** — Camelot Wheelハーモニックマッチングでキー互換性のあるサンプルを提案
- **ワンショットDrum Rack** — `build_drum_rack_for_key()` がキー互換サンプルの検索、Drum Rackトラック作成、キック／スネア／ハットのパッドへのロード、MIDIパターンの書き込みを1回の呼び出しで実行
- **Ableton連携** *(オプション)* — Liveでサンプルをプレビュー、Drum Rackパッドにロード。TCPセッション管理とアンドゥグループに対応

## アーキテクチャ

```
                    ┌──────────────────────────────────┐
  AI Agent          │   sample-librarian               │
    │               │   MCP Server (9 tools)           │
    │               │                                  │
    ├── librarian_search ──────────┐                   │
    ├── librarian_add_root          │ Core (standalone) │
    ├── librarian_list_roots        │                   │
    ├── librarian_index             ▼                   │
    ├── librarian_analyze      ┌─────────────┐          │
    ├── librarian_analyze_folder│  SQLite +   │          │
    ├── librarian_recommend     │  FTS5 (BM25)│          │
    │                           └─────────────┘          │
    ├── librarian_preview ──────┐                       │
    └── librarian_load_to_pad   │ Optional              │
                                │                       │
              build_drum_rack_for_key()                 │
              (Python API, not MCP tool)                │
                                │                       │
                    ┌──────────▼───────────────────────┐
                    │   live-agent-remote              │
                    │   LiveAgentClient (TCP 8765)     │
                    │   batch() / undo_group()         │
                    │   (Ableton Live)                 │
                    └──────────────────────────────────┘
```

**コアツールはAbletonなしで動作します。** 連携ツールはLiveAgentが起動しているかを自動検出し、未起動の場合はセットアップのガイドメッセージを表示します。

## クイックスタート

```bash
# セットアップ
git clone https://github.com/happytown-s/sample-librarian.git
cd sample-librarian
bash setup.sh

# サンプルフォルダを追加（追加時に自動インデックス）
.venv/bin/python3 -c "from mcp_server import librarian_add_root; print(librarian_add_root('~/Music/Ableton/User Library/Samples'))"

# または手動でインデックスを構築
.venv/bin/python3 -m librarian.index --root ~/path/to/samples

# 検索
.venv/bin/python3 -m librarian.search dark bass

# キー互換サンプルのおすすめ提案
.venv/bin/python3 -m librarian.recommend Fm kick --analyze
```

## データベース

Sample Librarianは **SQLite with FTS5**（BM25フルテキスト検索）をメインの
インデックスとして使用します。初回実行時、既存のJSONLインデックスは自動的に
SQLiteに移行されます。

**スキーマの主な内容:**
- `samples` — ファイルメタデータ（パス、名前、カテゴリ、サイズ、ハッシュ）
- `analysis_cache` — ピッチ、BPM、キー、再生時間、スペクトルデータ
- `tags` — 検索可能なタグ関連付け
- `roots` — 登録済みサンプルフォルダとスキャン履歴
- `samples_fts` — BM25検索用FTS5仮想テーブル

**AIフレンドリーなエンリッチメント**（`enrich_result()`）: すべての検索結果に
以下のフィールドが付加されます:

| フィールド | 説明 |
|------------|------|
| `key` | 検出された musical key |
| `bpm` | 検出されたテンポ |
| `pitch` | 基本周波数（音名＋音番号） |
| `sample_type` | oneshot / short_loop / medium_loop / long_loop |
| `is_atonal` | ピッチを持たないサンプル（ハイハット、ノイズ）の場合True |
| `confidence` | 分析の完全性に基づくヒューリスティックスコア 0.0–1.0 |
| `recommended_use` | このサンプルの使い方（例: "drum_kit_kick"） |
| `ableton_action` | 推奨されるLiveAgent呼び出し（例: `load_sample_to_pad`） |
| `compatible_keys` | Camelot Wheelによるハーモニック互換キー |

## 重複検出

4つの独立した軸で冗長なサンプルを検出します:

| 関数 | 方式 | 用途 |
|------|------|------|
| `find_duplicates_by_hash()` | コンテンツハッシュ（同一ファイル） | 完全な重複 |
| `find_similar_by_duration()` | 許容誤差内の再生時間＋同一カテゴリ | リエクスポートの可能性 |
| `find_similar_by_pitch()` | 同一ピッチクラス＋同一カテゴリ | 調性的な重複 |
| `find_similar_by_spectral()` | スペクトル重心の類似度 | 同一キャラクター／音色 |
| `find_all_duplicates()` | 上記すべてを統合 | 完全な監査 |

## ワンショットDrum Rack（`build_drum_rack_for_key()`）

すべてを統合するオーケストレーションAPI:

```python
from librarian.live_agent_bridge import build_drum_rack_for_key

result = build_drum_rack_for_key(
    key="Fm",                    # ターゲットキー
    track_index=-1,              # トラックリストの末尾に追加
    host="127.0.0.1",            # LiveAgentホスト
    port=8765,                   # LiveAgentポート
)
```

この1回の呼び出しで以下を実行します:
1. SQLiteインデックスからキー互換性のあるキック、スネア、ハイハットサンプルを検索
2. Ableton LiveにDrum Rackトラックを作成（デフォルト: 808 Core Kit）
3. サンプルをパッド36（キック）、38（スネア）、42（クローズドハット）にロード
4. 必要に応じて基本的なMIDIドラムパターンを書き込み
5. ロードしたサンプルのパスを返して検証可能

詳細な使い方は `docs/recipes.md` を参照してください。

## MCP Server（AIエージェント向け）

### Hermes Agent

`~/.hermes/profiles/<profile>/config.yaml` に以下を追加してください:

```yaml
mcp_servers:
  librarian:
    command: /path/to/sample-librarian/.venv/bin/python3
    args: [/path/to/sample-librarian/mcp_server.py]
```

### その他のMCPクライアント

お使いのMCPクライアントに以下を指定してください:
```
Command: /path/to/sample-librarian/.venv/bin/python3
Args: [/path/to/sample-librarian/mcp_server.py]
```

Claude Desktop、Cursor、その他のクライアントについては `docs/mcp-clients.md` を参照してください。

## MCPツール（全9種）

### コア（常に利用可能）

- `librarian_search` — キーワード、カテゴリ、拡張子でインデックスを検索
- `librarian_add_root` — フォルダを設定に追加＋自動再インデックス
- `librarian_list_roots` — 設定済みルートとインデックス状態を表示
- `librarian_index` — フォルダからサンプルインデックスを構築／再構築
- `librarian_analyze` — ファイルを分析：ピッチ、BPM、キー、再生時間
- `librarian_analyze_folder` — フォルダを一括分析（ピッチ順でソート）
- `librarian_recommend` — Camelot Wheelによるキー互換おすすめ提案

### オプション連携（live-agent-remoteが必要）

- `librarian_preview` — サンプルをAbleton Liveのオーディオクリップとして読み込み
- `librarian_load_to_pad` — サンプルをDrum Rackパッドにロード

連携ツールはLiveAgentの起動状態を自動検出します。利用できない場合はセットアップ手順を含むエラーメッセージを返しますが、コアツールは引き続き完全に機能します。

## live-agent-remoteと組み合わせて使う

この2つのプロジェクトは **独立していますが相補的な関係** にあります:

- **sample-librarian** — サンプルの検索、分析、おすすめ提案
- **live-agent-remote** — Ableton Liveの操作（MIDI、クリップ、デバイス）

両方のMCPサーバーをAIエージェントの設定に登録してください:

```yaml
mcp_servers:
  liveagent:
    command: /path/to/live-agent-remote/.venv/bin/python3
    args: [/path/to/live-agent-remote/mcp_server.py]
  librarian:
    command: /path/to/sample-librarian/.venv/bin/python3
    args: [/path/to/sample-librarian/mcp_server.py]
```

### 典型的なワークフロー

```
0. librarian_add_root("~/Music/Ableton/User Library/Samples")  → フォルダ登録＋インデックス
1. librarian_recommend("Fm", category="Kick")     → 互換性のあるキックを取得
2. librarian_preview("/path/to/kick.wav")          → Abletonでプレビュー
3. librarian_load_to_pad("/path/to/kick.wav", ...) → Drum Rackにロード
4. mcp_liveagent_write_midi_notes(...)              → ドラムパターンを書き込み

# または1〜4を1回で実行:
build_drum_rack_for_key(key="Fm")  → 検索 + 作成 + ロード + MIDI
```

## 設定

`config.local.py`（gitignore対象）を編集してください:

```python
# 必須: インデックス対象のサンプルフォルダ
SAMPLES_ROOTS = [
    "~/Music/Ableton/User Library/Samples",
    "/path/to/your/sample/library",
]

# オプション: LiveAgent連携
LIVEAGENT_HOST = "127.0.0.1"
LIVEAGENT_PORT = 8765
```

または環境変数を使用:

```bash
export SAMPLES_PATH="/path/to/samples"
export LIVEAGENT_HOST=127.0.0.1
export LIVEAGENT_PORT=8765
```

## Camelot Wheelハーモニックマッチング

おすすめ提案はCamelot Wheelシステムを使用します:

- 同じ番号、同じ文字 — 完全一致
- 隣接番号±1、同じ文字 — スムーズな転調
- 同じ番号、異なる文字 — 平行長短調
- 無調性サンプル（ハイハット、ノイズ）は常に含まれます

## CLIの使い方

```bash
# インデックス
python3 -m librarian.index --root ~/samples --root ~/more/samples
python3 -m librarian.index --query bass --query dark

# 検索
python3 -m librarian.search dark bass --limit 10
python3 -m librarian.search 808 kick --category Kick --json

# 分析
python3 -m librarian.analyze file.wav --mode full
python3 -m librarian.analyze ./folder/ --mode pitch

# おすすめ提案
python3 -m librarian.recommend Fm kick --analyze
python3 -m librarian.recommend C --category Bass

# データベース（重複検出、移行）
python3 -m librarian.db --duplicates
python3 -m librarian.db --migrate data/samples_index.jsonl
python3 -m librarian.db --stats
```

## ドキュメント

- `docs/recipes.md` — 一般的なワークフローとコードレシピ
- `docs/security.md` — セキュリティモデルと安全な操作
- `docs/troubleshooting.md` — デバッグガイド
- `docs/mcp-clients.md` — Claude Desktop、Cursor、その他のセットアップ

## テスト

```bash
.venv/bin/python3 -m pytest tests/ -v
```

データベース操作、検索、分析、エンリッチメントをカバーする22のテスト。
CIはGitHub Actionsで ruff lint + pytest を実行します。

## 必要環境

- Python 3.10+
- librosa, numpy, scipy, soundfile（setup.shで自動インストール）
- mcp（MCPサーバー用）
- オプション: [live-agent-remote](https://github.com/happytown-s/live-agent-remote)（Ableton連携用）

## ライセンス

MIT
