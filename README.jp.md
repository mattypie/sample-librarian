<p align="center">
  <img src="assets/banner.jpg" alt="Sample Librarian" width="640">
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
単体でも動作しますが、[live-agent-remote](https://github.com/happytown-s/live-agent-remote) と連携してAbleton Liveでのプレビューも可能です。

## 機能

- **ルート管理** — サンプルフォルダを設定に追加すると自動で再インデックス
- **インデックス** — サンプルフォルダをスキャンし、メタデータ（カテゴリ、タグ、ファイル情報）を抽出
- **検索** — スコアリング付きキーワード検索（名前 > カテゴリ > タグ > コンテンツ）
- **分析** — librosaベースのピッチ検出、BPM、キー推定
- **おすすめ提案** — Camelot Wheelによる調性互換性マッチングでキーが合うサンプルを提案
- **Ableton連携** *(オプション)* — Liveでサンプルをプレビュー、Drum Rackパッドにロード

## アーキテクチャ

```
                    ┌──────────────────────────┐
  AI Agent          │   sample-librarian       │
    │               │   MCP Server (9 tools)   │
    ├── librarian_search        ──┐            │
    ├── librarian_add_root        │ Core (standalone)
    ├── librarian_list_roots      │            │
    ├── librarian_analyze         │            │
    ├── librarian_recommend       │            │
    ├── librarian_preview ────────┤            │
    └── librarian_load_to_pad     │ Optional   │
                                 ──┘           │
                    └──────────┬───────────────┘
                               │ TCP (optional)
                    ┌──────────▼───────────────┐
                    │   live-agent-remote      │
                    │   (Ableton Live)         │
                    └──────────────────────────┘
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

## MCP Server（AIエージェント向け）

### Hermes Agent

`~/.hermes/profiles/<profile>/config.yaml` に以下を追加してください：

```yaml
mcp_servers:
  librarian:
    command: /path/to/sample-librarian/.venv/bin/python3
    args: [/path/to/sample-librarian/mcp_server.py]
```

### その他のMCPクライアント

お使いのMCPクライアントに以下を指定してください：
```
Command: /path/to/sample-librarian/.venv/bin/python3
Args: [/path/to/sample-librarian/mcp_server.py]
```

## MCPツール（全9種）

### コア（常に利用可能）

| ツール | 説明 |
|--------|------|
| `librarian_search` | キーワード、カテゴリ、拡張子でインデックスを検索 |
| `librarian_add_root` | フォルダを設定に追加＋自動再インデックス |
| `librarian_list_roots` | 設定済みルートとインデックス状態を表示 |
| `librarian_index` | フォルダからサンプルインデックスを構築／再構築 |
| `librarian_analyze` | ファイルを分析：ピッチ、BPM、キー、再生時間 |
| `librarian_analyze_folder` | フォルダを一括分析（ピッチ順でソート） |
| `librarian_recommend` | Camelot Wheelによるキー互換おすすめ提案 |

### オプション連携（live-agent-remoteが必要）

| ツール | 説明 |
|--------|------|
| `librarian_preview` | サンプルをAbleton Liveのオーディオクリップとして読み込み |
| `librarian_load_to_pad` | サンプルをDrum Rackパッドにロード |

連携ツールはLiveAgentの起動状態を自動検出します。利用できない場合はセットアップ手順を含むエラーメッセージを返しますが、コアツールは引き続き完全に機能します。

## live-agent-remoteと組み合わせて使う

この2つのプロジェクトは **独立していますが相補的な関係** にあります：

| プロジェクト | 役割 |
|-------------|------|
| **sample-librarian** | サンプルの検索、分析、おすすめ提案 |
| **live-agent-remote** | Ableton Liveの操作（MIDI、クリップ、デバイス） |

両方のMCPサーバーをAIエージェントの設定に登録してください：

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
0. librarian_add_root("~/Music/Ableton/User Library/Samples")  → フォルダ登録＋自動インデックス
1. librarian_recommend("Fm", category="Kick")     → 互換性のあるキックを取得
2. librarian_preview("/path/to/kick.wav")          → Abletonでプレビュー
3. librarian_load_to_pad("/path/to/kick.wav", ...) → Drum Rackにロード
4. mcp_liveagent_write_midi_notes(...)              → ドラムパターンを書き込み
```

## 設定

`config.local.py`（gitignore対象）を編集してください：

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

または環境変数を使用：

```bash
export SAMPLES_PATH="/path/to/samples"
export LIVEAGENT_HOST=127.0.0.1
export LIVEAGENT_PORT=8765
```

## Camelot Wheelハーモニックマッチング

おすすめ提案はCamelot Wheelシステムを使用します：

| キー | Camelot | 互換性のあるキー |
|------|---------|-----------------|
| Fm   | 4B      | Ebm(3B), C#m(5B), F(4A) |
| C    | 8A      | F(7A), G(9A), Am(8B) |

**ルール：**
- 同じ番号、同じ文字（完全一致）
- 隣接番号±1、同じ文字（スムーズな転調）
- 同じ番号、異なる文字（平行長短調）
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
```

## 必要環境

- Python 3.10+
- librosa, numpy, scipy, soundfile（setup.shで自動インストール）
- mcp（MCPサーバー用）
- オプション: [live-agent-remote](https://github.com/happytown-s/live-agent-remote)（Ableton連携用）

## ライセンス

MIT
