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

Busca, analiza y recomienda samples de audio desde tu biblioteca local.
SQLite + FTS5 para búsqueda de texto completo, enriquecimiento de resultados
amigable para IA, detección de duplicados y coincidencia armónica Camelot Wheel.
Funciona de forma independiente, o se integra con [live-agent-remote](https://github.com/happytown-s/live-agent-remote)
para previsualización en Ableton Live y construcción de Drum Racks de one-shots.

## Características

- **Gestión de raíces** — Añade carpetas de samples a la configuración con reindexado automático
- **Indexado** — Escanea carpetas, extrae metadatos, los almacena en SQLite con FTS5
- **Búsqueda** — Búsqueda de texto completo BM25 en nombre, categoría, etiquetas y rutas de carpeta
- **Resultados enriquecidos** — Cada resultado de búsqueda incluye `key`, `bpm`, `pitch`, `confidence`, `recommended_use` y `ableton_action` — listos para que los agentes de IA actúen sobre ellos
- **Análisis** — Detección de tono basada en librosa, BPM, estimación de tonalidad, análisis espectral
- **Detección de duplicados** — Encuentra duplicados en 4 ejes: hash de contenido, duración, tono y huella espectral
- **Recomendación** — Coincidencia armónica Camelot Wheel para samples compatibles por tonalidad
- **Drum Rack de one-shots** — `build_drum_rack_for_key()` busca samples compatibles, crea una pista de Drum Rack, carga kick/snare/hat en los pads y escribe un patrón MIDI — todo en una sola llamada
- **Integración con Ableton** *(opcional)* — Previsualiza samples en Live, cárgalos en pads de Drum Rack, con gestión de sesiones TCP y grupos de undo

## Arquitectura

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

**Las herramientas principales funcionan sin Ableton.** Las herramientas de integración
detectan automáticamente si LiveAgent está en ejecución y muestran mensajes útiles
de configuración si no lo está.

## Inicio rápido

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

## Base de datos

Sample Librarian utiliza **SQLite con FTS5** (búsqueda de texto completo BM25) como su
índice principal. En la primera ejecución, los índices JSONL existentes se migran
automáticamente a SQLite.

**Aspectos destacados del esquema:**
- `samples` — metadatos de archivo (ruta, nombre, categoría, tamaño, hash)
- `analysis_cache` — tono, BPM, tonalidad, duración, datos espectrales
- `tags` — asociaciones de etiquetas buscables
- `roots` — carpetas de samples registradas con historial de escaneo
- `samples_fts` — tabla virtual FTS5 para búsqueda BM25

**Enriquecimiento amigable para IA** (`enrich_result()`): cada resultado de búsqueda se
aumenta con:

| Campo | Descripción |
|-------|-------------|
| `key` | Tonalidad musical detectada |
| `bpm` | Tempo detectado |
| `pitch` | Tono fundamental (nombre de nota + número) |
| `sample_type` | oneshot / short_loop / medium_loop / long_loop |
| `is_atonal` | True para samples no tonales (hi-hats, ruido) |
| `confidence` | Puntuación heurística 0.0–1.0 basada en la completitud del análisis |
| `recommended_use` | Cómo usar este sample (ej., "drum_kit_kick") |
| `ableton_action` | Llamada LiveAgent sugerida (ej., `load_sample_to_pad`) |
| `compatible_keys` | Tonalidades compatibles armónicamente vía Camelot Wheel |

## Detección de duplicados

Encuentra samples redundantes en cuatro ejes independientes:

| Función | Método | Caso de uso |
|----------|--------|-------------|
| `find_duplicates_by_hash()` | Hash de contenido (archivos idénticos) | Duplicados exactos |
| `find_similar_by_duration()` | Duración dentro de tolerancia + misma categoría | Posibles re-exportaciones |
| `find_similar_by_pitch()` | Misma clase de tono + misma categoría | Superposición tonal |
| `find_similar_by_spectral()` | Similitud de centroide espectral | Mismo carácter/timbre |
| `find_all_duplicates()` | Todos los anteriores combinados | Auditoría completa |

## Drum Rack de one-shots (`build_drum_rack_for_key()`)

La API de orquestación que lo une todo:

```python
from librarian.live_agent_bridge import build_drum_rack_for_key

result = build_drum_rack_for_key(
    key="Fm",                    # target key
    track_index=-1,              # append to end of track list
    host="127.0.0.1",            # LiveAgent host
    port=8765,                   # LiveAgent port
)
```

Esta única llamada:
1. Busca en el índice SQLite samples de kick, snare e hi-hat compatibles por tonalidad
2. Crea una pista de Drum Rack en Ableton Live (por defecto: 808 Core Kit)
3. Carga los samples en los pads 36 (kick), 38 (snare), 42 (closed hat)
4. Opcionalmente escribe un patrón de batería MIDI básico
5. Devuelve las rutas de los samples cargados para verificación

Consulta `docs/recipes.md` para uso detallado.

## Servidor MCP (para agentes de IA)

### Hermes Agent

Añade a `~/.hermes/profiles/<profile>/config.yaml`:

```yaml
mcp_servers:
  librarian:
    command: /path/to/sample-librarian/.venv/bin/python3
    args: [/path/to/sample-librarian/mcp_server.py]
```

### Otros clientes MCP

Apunta tu cliente MCP a:
```
Command: /path/to/sample-librarian/.venv/bin/python3
Args: [/path/to/sample-librarian/mcp_server.py]
```

Consulta `docs/mcp-clients.md` para Claude Desktop, Cursor y otros clientes.

## Herramientas MCP (9 en total)

### Principales (siempre disponibles)

- `librarian_search` — Busca en el índice por palabras clave, categoría, extensión
- `librarian_add_root` — Añade carpeta a la configuración + reindexado automático
- `librarian_list_roots` — Muestra las raíces configuradas y el estado del índice
- `librarian_index` — Construye/reconstruye el índice de samples desde las carpetas
- `librarian_analyze` — Analiza archivo: tono, BPM, tonalidad, duración
- `librarian_analyze_folder` — Análisis por lotes de carpeta (ordenado por tono)
- `librarian_recommend` — Recomendaciones compatibles por tonalidad Camelot Wheel

### Integración opcional (requiere live-agent-remote)

- `librarian_preview` — Importa sample como clip de audio en Ableton Live
- `librarian_load_to_pad` — Carga sample en un pad de Drum Rack

Las herramientas de integración detectan automáticamente si LiveAgent está en ejecución.
Si no está disponible, devuelven un error útil con instrucciones de configuración — las
herramientas principales siguen funcionando completamente.

## Uso con live-agent-remote

Estos dos proyectos son **independientes pero complementarios**:

- **sample-librarian** — Buscar, analizar, recomendar samples
- **live-agent-remote** — Controlar Ableton Live (MIDI, clips, dispositivos)

Registra ambos servidores MCP en la configuración de tu agente de IA:

```yaml
mcp_servers:
  liveagent:
    command: /path/to/live-agent-remote/.venv/bin/python3
    args: [/path/to/live-agent-remote/mcp_server.py]
  librarian:
    command: /path/to/sample-librarian/.venv/bin/python3
    args: [/path/to/sample-librarian/mcp_server.py]
```

### Flujo de trabajo típico

```
0. librarian_add_root("~/Music/Ableton/User Library/Samples")  → register + index
1. librarian_recommend("Fm", category="Kick")     → compatible kicks
2. librarian_preview("/path/to/kick.wav")          → preview in Ableton
3. librarian_load_to_pad("/path/to/kick.wav", ...) → load onto Drum Rack
4. mcp_liveagent_write_midi_notes(...)              → write drum pattern

# Or do 1-4 in one shot:
build_drum_rack_for_key(key="Fm")  → search + create + load + MIDI
```

## Configuración

Edita `config.local.py` (gitignored):

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

O usa variables de entorno:

```bash
export SAMPLES_PATH="/path/to/samples"
export LIVEAGENT_HOST=127.0.0.1
export LIVEAGENT_PORT=8765
```

## Coincidencia armónica Camelot Wheel

Las recomendaciones utilizan el sistema Camelot Wheel:

- Mismo número, misma letra — coincidencia perfecta
- Número adyacente ±1, misma letra — transición suave
- Mismo número, letra opuesta — relativo mayor/menor
- Los samples atonales (hi-hats, ruido) siempre se incluyen

## Uso de CLI

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

## Documentación

- `docs/recipes.md` — Flujos de trabajo comunes y recetas de código
- `docs/security.md` — Modelo de seguridad y operaciones seguras
- `docs/troubleshooting.md` — Guía de depuración
- `docs/mcp-clients.md` — Configuración para Claude Desktop, Cursor y otros

## Tests

```bash
.venv/bin/python3 -m pytest tests/ -v
```

22 tests cubren operaciones de base de datos, búsqueda, análisis y enriquecimiento.
La CI se ejecuta en GitHub Actions con ruff lint + pytest.

## Requisitos

- Python 3.10+
- librosa, numpy, scipy, soundfile (instalados automáticamente por setup.sh)
- mcp (para el servidor MCP)
- Opcional: [live-agent-remote](https://github.com/happytown-s/live-agent-remote) para la integración con Ableton

## Licencia

MIT
