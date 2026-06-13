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

搜索、分析和推荐本地音频采样库中的音频采样。
可独立运行，也可与 [live-agent-remote](https://github.com/happytown-s/live-agent-remote) 集成，在 Ableton Live 中进行预览。

## 功能特性

- **管理采样根目录** — 将采样文件夹添加到配置，自动重新索引
- **索引** — 扫描任意采样文件夹，提取元数据（类别、标签、文件信息）
- **搜索** — 带评分的关键词搜索（名称 > 类别 > 标签 > 内容）
- **分析** — 基于 librosa 的音高检测、BPM 和调性估算
- **推荐** — 基于 Camelot Wheel 调性匹配，推荐调性兼容的采样
- **Ableton 集成** *(可选)* — 在 Live 中预览采样，加载到 Drum Rack 垫片上

## 架构

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

**核心工具无需 Ableton 即可使用。** 集成工具会自动检测 LiveAgent 是否正在运行，若未运行则提供有用的设置提示。

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

## MCP 工具（共 9 个）

### 核心（始终可用）

| 工具 | 说明 |
|------|-------------|
| `librarian_search` | 按关键词、类别、扩展名搜索索引 |
| `librarian_add_root` | 添加文件夹到配置并自动重新索引 |
| `librarian_list_roots` | 显示已配置的根目录及索引状态 |
| `librarian_index` | 从文件夹构建/重建采样索引 |
| `librarian_analyze` | 分析文件：音高、BPM、调性、时长 |
| `librarian_analyze_folder` | 批量分析文件夹（按音高排序） |
| `librarian_recommend` | 基于 Camelot Wheel 调性兼容推荐 |

### 可选集成（需要 live-agent-remote）

| 工具 | 说明 |
|------|-------------|
| `librarian_preview` | 将采样作为音频片段导入 Ableton Live |
| `librarian_load_to_pad` | 将采样加载到 Drum Rack 垫片上 |

集成工具会自动检测 LiveAgent 是否正在运行。如果不可用，它们会返回带有设置说明的错误提示——核心工具仍然完全可用。

## 与 live-agent-remote 配合使用

这两个项目**独立但互补**：

| 项目 | 角色 |
|---------|------|
| **sample-librarian** | 搜索、分析、推荐采样 |
| **live-agent-remote** | 控制 Ableton Live（MIDI、片段、设备） |

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

| 调性 | Camelot | 兼容调性 |
|-----|---------|-----------------|
| Fm  | 4B      | Ebm(3B), C#m(5B), F(4A) |
| C   | 8A      | F(7A), G(9A), Am(8B) |

**规则：**
- 相同编号，相同字母（完美匹配）
- 相邻编号 ±1，相同字母（平滑过渡）
- 相同编号，相反字母（关系大调/小调）
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
```

## 环境要求

- Python 3.10+
- librosa、numpy、scipy、soundfile（由 setup.sh 自动安装）
- mcp（用于 MCP 服务器）
- 可选：[live-agent-remote](https://github.com/happytown-s/live-agent-remote) 用于 Ableton 集成

## 许可证

MIT
