<p align="center">
  <img src="assets/banner_en.jpg" alt="Sample Librarian" width="640">
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

搜索、分析和推荐本地音频采样库中的音频采样。
基于 SQLite + FTS5 全文搜索、AI 友好的结果增强、重复检测，
以及 Camelot Wheel 调性匹配。可独立运行，也可与
[live-agent-remote](https://github.com/happytown-s/live-agent-remote)
集成，在 Ableton Live 中进行预览和一键构建 Drum Rack。

## 功能特性

- **管理采样根目录** — 将采样文件夹添加到配置，自动重新索引
- **索引** — 扫描文件夹，提取元数据，存储到 SQLite with FTS5
- **搜索** — BM25 全文搜索，覆盖名称、类别、标签和文件夹路径
- **增强结果** — 每个搜索结果包含 `key`、`bpm`、`pitch`、`confidence`、`recommended_use` 和 `ableton_action` — 让 AI agent 可以直接行动
- **分析** — 基于 librosa 的音高检测、BPM、调性估算、频谱分析
- **重复检测** — 从 4 个维度查找重复：内容哈希、时长、音高和频谱指纹
- **推荐** — 基于 Camelot Wheel 调性匹配，推荐调性兼容的采样
- **一键 Drum Rack** — `build_drum_rack_for_key()` 搜索兼容采样、创建 Drum Rack 轨道、将 kick/snare/hat 加载到垫片、并编写 MIDI 模式 — 全部一次调用完成
- **Ableton 集成** *(可选)* — 在 Live 中预览采样，加载到 Drum Rack 垫片上，支持 TCP 会话管理和撤销组

## 架构

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

**核心工具无需 Ableton 即可使用。** 集成工具会自动检测
LiveAgent 是否正在运行，若未运行则提供有用的设置提示。

## 快速开始

```bash
# 安装
git clone https://github.com/happytown-s/sample-librarian.git
cd sample-librarian
bash setup.sh

# 添加采样文件夹（添加时自动索引）
.venv/bin/python3 -c "from mcp_server import librarian_add_root; print(librarian_add_root('~/Music/Ableton/User Library/Samples'))"

# 或手动构建索引
.venv/bin/python3 -m librarian.index --root ~/path/to/samples

# 搜索
.venv/bin/python3 -m librarian.search dark bass

# 推荐调性兼容的采样
.venv/bin/python3 -m librarian.recommend Fm kick --analyze
```

## 数据库

Sample Librarian 使用 **SQLite with FTS5**（BM25 全文搜索）作为
主索引。首次运行时，现有的 JSONL 索引会自动迁移到 SQLite。

**Schema 概要：**
- `samples` — 文件元数据（路径、名称、类别、大小、哈希）
- `analysis_cache` — 音高、BPM、调性、时长、频谱数据
- `tags` — 可搜索的标签关联
- `roots` — 已注册的采样文件夹及扫描历史
- `samples_fts` — 用于 BM25 搜索的 FTS5 虚拟表

**AI 友好的增强**（`enrich_result()`）：每个搜索结果都会
补充以下字段：

| 字段 | 说明 |
|-------|-------------|
| `key` | 检测到的调性 |
| `bpm` | 检测到的速度 |
| `pitch` | 基础音高（音名 + 编号） |
| `sample_type` | oneshot / short_loop / medium_loop / long_loop |
| `is_atonal` | 无调性采样为 True（hi-hat、噪音） |
| `confidence` | 基于分析完整度的启发式评分 0.0–1.0 |
| `recommended_use` | 如何使用此采样（例如 "drum_kit_kick"） |
| `ableton_action` | 建议的 LiveAgent 调用（例如 `load_sample_to_pad`） |
| `compatible_keys` | 通过 Camelot Wheel 计算的调性兼容键 |

## 重复检测

从四个独立维度查找冗余采样：

| 函数 | 方法 | 用途 |
|----------|--------|----------|
| `find_duplicates_by_hash()` | 内容哈希（完全相同的文件） | 精确重复 |
| `find_similar_by_duration()` | 时长在容差范围内 + 相同类别 | 潜在再导出 |
| `find_similar_by_pitch()` | 相同音高类别 + 相同类别 | 调性重叠 |
| `find_similar_by_spectral()` | 频谱质心相似度 | 相同特征/音色 |
| `find_all_duplicates()` | 以上全部组合 | 全面审计 |

## 一键 Drum Rack（`build_drum_rack_for_key()`）

将所有功能串联起来的编排 API：

```python
from librarian.live_agent_bridge import build_drum_rack_for_key

result = build_drum_rack_for_key(
    key="Fm",                    # 目标调性
    track_index=-1,              # 追加到轨道列表末尾
    host="127.0.0.1",            # LiveAgent 主机
    port=8765,                   # LiveAgent 端口
)
```

这一次调用会：
1. 在 SQLite 索引中搜索与目标调性兼容的 kick、snare 和 hi-hat 采样
2. 在 Ableton Live 中创建 Drum Rack 轨道（默认：808 Core Kit）
3. 将采样加载到垫片 36（kick）、38（snare）、42（closed hat）
4. 可选地编写基本 MIDI 鼓点模式
5. 返回已加载的采样路径以供验证

详细用法请参见 `docs/recipes.md`。

## MCP 服务器（用于 AI Agent）

### Hermes Agent

添加到 `~/.hermes/profiles/<profile>/config.yaml`：

```yaml
mcp_servers:
  librarian:
    command: /path/to/sample-librarian/.venv/bin/python3
    args: [/path/to/sample-librarian/mcp_server.py]
```

### 其他 MCP 客户端

将您的 MCP 客户端指向：
```
Command: /path/to/sample-librarian/.venv/bin/python3
Args: [/path/to/sample-librarian/mcp_server.py]
```

Claude Desktop、Cursor 等客户端的设置请参见 `docs/mcp-clients.md`。

## MCP 工具（共 9 个）

### 核心（始终可用）

- `librarian_search` — 按关键词、类别、扩展名搜索索引
- `librarian_add_root` — 添加文件夹到配置并自动重新索引
- `librarian_list_roots` — 显示已配置的根目录及索引状态
- `librarian_index` — 从文件夹构建/重建采样索引
- `librarian_analyze` — 分析文件：音高、BPM、调性、时长
- `librarian_analyze_folder` — 批量分析文件夹（按音高排序）
- `librarian_recommend` — 基于 Camelot Wheel 调性兼容推荐

### 可选集成（需要 live-agent-remote）

- `librarian_preview` — 将采样作为音频片段导入 Ableton Live
- `librarian_load_to_pad` — 将采样加载到 Drum Rack 垫片上

集成工具会自动检测 LiveAgent 是否正在运行。如果不可用，
它们会返回带有设置说明的错误提示——核心工具仍然完全可用。

## 与 live-agent-remote 配合使用

这两个项目**独立但互补**：

- **sample-librarian** — 搜索、分析、推荐采样
- **live-agent-remote** — 控制 Ableton Live（MIDI、片段、设备）

在您的 AI agent 配置中注册两个 MCP 服务器：

```yaml
mcp_servers:
  liveagent:
    command: /path/to/live-agent-remote/.venv/bin/python3
    args: [/path/to/live-agent-remote/mcp_server.py]
  librarian:
    command: /path/to/sample-librarian/.venv/bin/python3
    args: [/path/to/sample-librarian/mcp_server.py]
```

### 典型工作流程

```
0. librarian_add_root("~/Music/Ableton/User Library/Samples")  → 注册文件夹 + 自动索引
1. librarian_recommend("Fm", category="Kick")     → 获取兼容的 kick
2. librarian_preview("/path/to/kick.wav")          → 在 Ableton 中预览
3. librarian_load_to_pad("/path/to/kick.wav", ...) → 加载到 Drum Rack
4. mcp_liveagent_write_midi_notes(...)              → 编写鼓点模式

# 或一步完成 1-4：
build_drum_rack_for_key(key="Fm")  → 搜索 + 创建 + 加载 + MIDI
```

## 配置

编辑 `config.local.py`（已被 gitignore 忽略）：

```python
# 必需：要索引的采样文件夹
SAMPLES_ROOTS = [
    "~/Music/Ableton/User Library/Samples",
    "/path/to/your/sample/library",
]

# 可选：LiveAgent 集成
LIVEAGENT_HOST = "127.0.0.1"
LIVEAGENT_PORT = 8765
```

或使用环境变量：

```bash
export SAMPLES_PATH="/path/to/samples"
export LIVEAGENT_HOST=127.0.0.1
export LIVEAGENT_PORT=8765
```

## Camelot Wheel 调性匹配

推荐功能使用 Camelot Wheel 系统：

- 相同编号，相同字母 — 完美匹配
- 相邻编号 ±1，相同字母 — 平滑过渡
- 相同编号，相反字母 — 关系大调/小调
- 无调性采样（hi-hat、噪音）始终包含在内

## 命令行使用

```bash
# 索引
python3 -m librarian.index --root ~/samples --root ~/more/samples
python3 -m librarian.index --query bass --query dark

# 搜索
python3 -m librarian.search dark bass --limit 10
python3 -m librarian.search 808 kick --category Kick --json

# 分析
python3 -m librarian.analyze file.wav --mode full
python3 -m librarian.analyze ./folder/ --mode pitch

# 推荐
python3 -m librarian.recommend Fm kick --analyze
python3 -m librarian.recommend C --category Bass

# 数据库（重复检测、迁移）
python3 -m librarian.db --duplicates
python3 -m librarian.db --migrate data/samples_index.jsonl
python3 -m librarian.db --stats
```

## 文档

- `docs/recipes.md` — 常用工作流程和代码示例
- `docs/security.md` — 安全模型和安全操作
- `docs/troubleshooting.md` — 调试指南
- `docs/mcp-clients.md` — Claude Desktop、Cursor 等客户端的设置

## 测试

```bash
.venv/bin/python3 -m pytest tests/ -v
```

共 22 个测试，覆盖数据库操作、搜索、分析和结果增强。
CI 通过 GitHub Actions 运行 ruff 代码检查 + pytest。

## 环境要求

- Python 3.10+
- librosa、numpy、scipy、soundfile（由 setup.sh 自动安装）
- mcp（用于 MCP 服务器）
- 可选：[live-agent-remote](https://github.com/happytown-s/live-agent-remote) 用于 Ableton 集成

## 许可证

MIT
