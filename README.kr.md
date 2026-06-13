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

로컬 라이브러리에서 오디오 샘플을 검색, 분석 및 추천합니다.
단독으로 작동하거나 [live-agent-remote](https://github.com/happytown-s/live-agent-remote)와 연동하여 Ableton Live에서 미리듣기를 지원합니다.

## 주요 기능

- **루트 관리** — 샘플 폴더를 config에 추가하면 자동으로 재인덱싱
- **인덱싱** — 샘플 폴더를 스캔하여 메타데이터(카테고리, 태그, 파일 정보) 추출
- **검색** — 점수 기반 키워드 검색 (이름 > 카테고리 > 태그 > 콘텐츠)
- **분석** — librosa 기반 피치 감지, BPM, 키 추정
- **추천** — Camelot Wheel 화성 매칭으로 키 호환 샘플 추천
- **Ableton 연동** *(선택)* — Live에서 샘플 미리듣기, Drum Rack 패드에 로드

## 아키텍처

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

**핵심 도구는 Ableton 없이도 작동합니다.** 연동 도구는 LiveAgent 실행 여부를 자동으로 감지하며,
실행 중이 아닌 경우 도움말 설정 메시지를 제공합니다.

## 빠른 시작

```bash
# Setup
git clone https://github.com/happytown-s/sample-librarian.git
cd sample-librarian
bash setup.sh

# Add sample folders (auto-indexes on add)
.venv/bin/python3 -c "from mcp_server import librarian_add_root; print(librarian_add_root('~/Music/Ableton/User Library/Samples'))"

# Or build index manually
.venv/bin/python3 -m librarian.index --root ~/path/to/samples

# Search
.venv/bin/python3 -m librarian.search dark bass

# Recommend key-compatible samples
.venv/bin/python3 -m librarian.recommend Fm kick --analyze
```

## MCP Server (AI 에이전트용)

### Hermes Agent

`~/.hermes/profiles/<profile>/config.yaml`에 추가:

```yaml
mcp_servers:
  librarian:
    command: /path/to/sample-librarian/.venv/bin/python3
    args: [/path/to/sample-librarian/mcp_server.py]
```

### 기타 MCP 클라이언트

MCP 클라이언트에 다음을 지정:
```
Command: /path/to/sample-librarian/.venv/bin/python3
Args: [/path/to/sample-librarian/mcp_server.py]
```

## MCP Tools (총 9개)

### 핵심 (항상 사용 가능)

| Tool | 설명 |
|------|-------------|
| `librarian_search` | 키워드, 카테고리, 확장자로 인덱스 검색 |
| `librarian_add_root` | 폴더를 config에 추가 + 자동 재인덱싱 |
| `librarian_list_roots` | 설정된 루트 및 인덱스 상태 표시 |
| `librarian_index` | 폴더에서 샘플 인덱스 구축/재구축 |
| `librarian_analyze` | 파일 분석: 피치, BPM, 키, 길이 |
| `librarian_analyze_folder` | 폴더 일괄 분석 (피치순 정렬) |
| `librarian_recommend` | Camelot Wheel 키 호환 추천 |

### 선택 연동 (live-agent-remote 필요)

| Tool | 설명 |
|------|-------------|
| `librarian_preview` | Ableton Live에서 샘플을 오디오 클립으로 임포트 |
| `librarian_load_to_pad` | Drum Rack 패드에 샘플 로드 |

연동 도구는 LiveAgent 실행 여부를 자동 감지합니다. 사용할 수 없는 경우
설정 가이드가 포함된 유용한 에러를 반환하며, 핵심 도구는
완전히 작동합니다.

## live-agent-remote와 함께 사용하기

이 두 프로젝트는 **독립적이지만 상호 보완적**입니다:

| Project | 역할 |
|---------|------|
| **sample-librarian** | 샘플 검색, 분석, 추천 |
| **live-agent-remote** | Ableton Live 제어 (MIDI, 클립, 디바이스) |

AI 에이전트 config에 두 MCP 서버를 모두 등록:

```yaml
mcp_servers:
  liveagent:
    command: /path/to/live-agent-remote/.venv/bin/python3
    args: [/path/to/live-agent-remote/mcp_server.py]
  librarian:
    command: /path/to/sample-librarian/.venv/bin/python3
    args: [/path/to/sample-librarian/mcp_server.py]
```

### 일반적인 워크플로우

```
0. librarian_add_root("~/Music/Ableton/User Library/Samples")  → register folder + auto index
1. librarian_recommend("Fm", category="Kick")     → get compatible kicks
2. librarian_preview("/path/to/kick.wav")          → preview in Ableton
3. librarian_load_to_pad("/path/to/kick.wav", ...) → load onto Drum Rack
4. mcp_liveagent_write_midi_notes(...)              → write drum pattern
```

## 설정

`config.local.py`를 편집 (gitignored):

```python
# Required: sample folders to index
SAMPLES_ROOTS = [
    "~/Music/Ableton/User Library/Samples",
    "/path/to/your/sample/library",
]

# Optional: LiveAgent integration
LIVEAGENT_HOST = "127.0.0.1"
LIVEAGENT_PORT = 8765
```

또는 환경 변수 사용:

```bash
export SAMPLES_PATH="/path/to/samples"
export LIVEAGENT_HOST=127.0.0.1
export LIVEAGENT_PORT=8765
```

## Camelot Wheel 화성 매칭

추천은 Camelot Wheel 시스템을 사용합니다:

| Key | Camelot | 호환 키 |
|-----|---------|-----------------|
| Fm  | 4B      | Ebm(3B), C#m(5B), F(4A) |
| C   | 8A      | F(7A), G(9A), Am(8B) |

**규칙:**
- 같은 번호, 같은 문자 (완벽한 매치)
- 인접 번호 ±1, 같은 문자 (자연스러운 전환)
- 같은 번호, 반대 문자 (나란한 장/단조)
- 무조성 샘플 (하이햇, 노이즈)은 항상 포함

## CLI 사용법

```bash
# Index
python3 -m librarian.index --root ~/samples --root ~/more/samples
python3 -m librarian.index --query bass --query dark

# Search
python3 -m librarian.search dark bass --limit 10
python3 -m librarian.search 808 kick --category Kick --json

# Analyze
python3 -m librarian.analyze file.wav --mode full
python3 -m librarian.analyze ./folder/ --mode pitch

# Recommend
python3 -m librarian.recommend Fm kick --analyze
python3 -m librarian.recommend C --category Bass
```

## 요구사항

- Python 3.10+
- librosa, numpy, scipy, soundfile (setup.sh로 자동 설치)
- mcp (MCP server용)
- 선택: [live-agent-remote](https://github.com/happytown-s/live-agent-remote) (Ableton 연동용)

## License

MIT
