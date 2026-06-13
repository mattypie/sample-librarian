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

로컬 라이브러리에서 오디오 샘플을 검색, 분석 및 추천합니다.
SQLite + FTS5 전문 검색, AI 친화적 결과 보강, 중복 감지,
Camelot Wheel 화성 매칭을 지원합니다. 단독으로 작동하거나
[live-agent-remote](https://github.com/happytown-s/live-agent-remote)와 연동하여
Ableton Live에서 미리듣기 및 원샷 Drum Rack 빌드를 지원합니다.

## 주요 기능

- **루트 관리** — 샘플 폴더를 config에 추가하면 자동 재인덱싱
- **인덱싱** — 폴더를 스캔하여 메타데이터를 추출하고 SQLite + FTS5에 저장
- **검색** — 이름, 카테고리, 태그, 폴더 경로에 대한 BM25 전문 검색
- **보강된 결과** — 각 검색 결과에 `key`, `bpm`, `pitch`, `confidence`, `recommended_use`, `ableton_action` 포함 — AI 에이전트가 바로 실행할 수 있도록 준비됨
- **분석** — librosa 기반 피치 감지, BPM, 키 추정, 스펙트럼 분석
- **중복 감지** — 4가지 축으로 중복 검색: 콘텐츠 해시, 길이, 피치, 스펙트럼 핑거프린트
- **추천** — Camelot Wheel 화성 매칭으로 키 호환 샘플 추천
- **원샷 Drum Rack** — `build_drum_rack_for_key()`가 호환 샘플 검색, Drum Rack 트랙 생성, 킥/스네어/하이햇을 패드에 로드, MIDI 패턴 작성까지 한 번에 수행
- **Ableton 연동** *(선택)* — Live에서 샘플 미리듣기, Drum Rack 패드에 로드, TCP 세션 관리 및 undo 그룹 지원

## 아키텍처

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

**핵심 도구는 Ableton 없이도 작동합니다.** 연동 도구는 LiveAgent 실행 여부를
자동으로 감지하며, 실행 중이 아닌 경우 도움말 설정 메시지를 제공합니다.

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

## 데이터베이스

Sample Librarian는 **SQLite with FTS5**(BM25 전문 검색)를 기본 인덱스로
사용합니다. 최초 실행 시 기존 JSONL 인덱스는 자동으로 SQLite로 마이그레이션됩니다.

**스키마 하이라이트:**
- `samples` — 파일 메타데이터 (경로, 이름, 카테고리, 크기, 해시)
- `analysis_cache` — 피치, BPM, 키, 길이, 스펙트럼 데이터
- `tags` — 검색 가능한 태그 연결
- `roots` — 등록된 샘플 폴더 및 스캔 이력
- `samples_fts` — BM25 검색용 FTS5 가상 테이블

**AI 친화적 보강** (`enrich_result()`): 모든 검색 결과에 다음 정보가 추가됩니다:

| 필드 | 설명 |
|-------|-------------|
| `key` | 감지된 음악적 키 |
| `bpm` | 감지된 템포 |
| `pitch` | 기본 피치 (음명 + 번호) |
| `sample_type` | oneshot / short_loop / medium_loop / long_loop |
| `is_atonal` | 무조성 샘플 (하이햇, 노이즈)인 경우 True |
| `confidence` | 분석 완전도 기반 휴리스틱 점수 0.0–1.0 |
| `recommended_use` | 샘플 사용법 (예: "drum_kit_kick") |
| `ableton_action` | 제안된 LiveAgent 호출 (예: `load_sample_to_pad`) |
| `compatible_keys` | Camelot Wheel 기반 화성 호환 키 |

## 중복 감지

4가지 독립적인 축으로 중복 샘플을 찾습니다:

| 함수 | 방식 | 용도 |
|----------|--------|----------|
| `find_duplicates_by_hash()` | 콘텐츠 해시 (동일 파일) | 완전한 중복 |
| `find_similar_by_duration()` | 허용 오차 내 길이 + 동일 카테고리 | 재추출 가능성 |
| `find_similar_by_pitch()` | 동일 피치 클래스 + 동일 카테고리 | 음조 겹침 |
| `find_similar_by_spectral()` | 스펙트럼 중심 유사도 | 동일 특성/음색 |
| `find_all_duplicates()` | 위 모든 방식 결합 | 전체 감사 |

## 원샷 Drum Rack (`build_drum_rack_for_key()`)

모든 것을 하나로 묶는 오케스트레이션 API:

```python
from librarian.live_agent_bridge import build_drum_rack_for_key

result = build_drum_rack_for_key(
    key="Fm",                    # target key
    track_index=-1,              # append to end of track list
    host="127.0.0.1",            # LiveAgent host
    port=8765,                   # LiveAgent port
)
```

이 단일 호출로:
1. SQLite 인덱스에서 키 호환 킥, 스네어, 하이햇 샘플을 검색
2. Ableton Live에 Drum Rack 트랙 생성 (기본: 808 Core Kit)
3. 패드 36(킥), 38(스네어), 42(클로즈드 햇)에 샘플 로드
4. 기본 MIDI 드럼 패턴 작성 (선택 사항)
5. 로드된 샘플 경로 반환 (검증용)

자세한 사용법은 `docs/recipes.md`를 참조하세요.

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

Claude Desktop, Cursor 등의 클라이언트 설정은 `docs/mcp-clients.md`를 참조하세요.

## MCP Tools (총 9개)

### 핵심 (항상 사용 가능)

- `librarian_search` — 키워드, 카테고리, 확장자로 인덱스 검색
- `librarian_add_root` — 폴더를 config에 추가 + 자동 재인덱싱
- `librarian_list_roots` — 설정된 루트 및 인덱스 상태 표시
- `librarian_index` — 폴더에서 샘플 인덱스 구축/재구축
- `librarian_analyze` — 파일 분석: 피치, BPM, 키, 길이
- `librarian_analyze_folder` — 폴더 일괄 분석 (피치순 정렬)
- `librarian_recommend` — Camelot Wheel 키 호환 추천

### 선택 연동 (live-agent-remote 필요)

- `librarian_preview` — Ableton Live에서 샘플을 오디오 클립으로 임포트
- `librarian_load_to_pad` — Drum Rack 패드에 샘플 로드

연동 도구는 LiveAgent 실행 여부를 자동 감지합니다. 사용할 수 없는 경우
설정 가이드가 포함된 유용한 에러를 반환하며, 핵심 도구는
완전히 작동합니다.

## live-agent-remote와 함께 사용하기

이 두 프로젝트는 **독립적이지만 상호 보완적**입니다:

- **sample-librarian** — 샘플 검색, 분석, 추천
- **live-agent-remote** — Ableton Live 제어 (MIDI, 클립, 디바이스)

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
0. librarian_add_root("~/Music/Ableton/User Library/Samples")  → register + index
1. librarian_recommend("Fm", category="Kick")     → compatible kicks
2. librarian_preview("/path/to/kick.wav")          → preview in Ableton
3. librarian_load_to_pad("/path/to/kick.wav", ...) → load onto Drum Rack
4. mcp_liveagent_write_midi_notes(...)              → write drum pattern

# Or do 1-4 in one shot:
build_drum_rack_for_key(key="Fm")  → search + create + load + MIDI
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

- 같은 번호, 같은 문자 — 완벽한 매치
- 인접 번호 ±1, 같은 문자 — 자연스러운 전환
- 같은 번호, 반대 문자 — 나란한 장/단조
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

# Database (duplicate detection, migration)
python3 -m librarian.db --duplicates
python3 -m librarian.db --migrate data/samples_index.jsonl
python3 -m librarian.db --stats
```

## 문서

- `docs/recipes.md` — 일반적인 워크플로우 및 코드 레시피
- `docs/security.md` — 보안 모델 및 안전한 작업
- `docs/troubleshooting.md` — 디버깅 가이드
- `docs/mcp-clients.md` — Claude Desktop, Cursor 등의 설정

## 테스트

```bash
.venv/bin/python3 -m pytest tests/ -v
```

데이터베이스 작업, 검색, 분석 및 보강을 다루는 22개의 테스트가 있습니다.
CI는 GitHub Actions에서 ruff lint + pytest로 실행됩니다.

## 요구사항

- Python 3.10+
- librosa, numpy, scipy, soundfile (setup.sh로 자동 설치)
- mcp (MCP server용)
- 선택: [live-agent-remote](https://github.com/happytown-s/live-agent-remote) (Ableton 연동용)

## License

MIT
