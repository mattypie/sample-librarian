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

Busca, analiza y recomienda samples de audio desde tu biblioteca local.
Funciona de forma independiente, o se integra con [live-agent-remote](https://github.com/happytown-s/live-agent-remote) para previsualización en Ableton Live.

## Características

- **Gestión de raíces** — Añade carpetas de samples a la configuración con reindexado automático
- **Indexado** — Escanea cualquier carpeta de samples, extrae metadatos (categoría, etiquetas, información de archivo)
- **Búsqueda** — Búsqueda por palabras clave con puntuación (nombre > categoría > etiquetas > contenido)
- **Análisis** — Detección de tono basada en librosa, BPM, estimación de tonalidad
- **Recomendación** — Coincidencia armónica Camelot Wheel para samples compatibles por tonalidad
- **Integración con Ableton** *(opcional)* — Previsualiza samples en Live, cárgalos en pads de Drum Rack

## Arquitectura

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

## Herramientas MCP (9 en total)

### Principales (siempre disponibles)

| Herramienta | Descripción |
|------|-------------|
| `librarian_search` | Busca en el índice por palabras clave, categoría, extensión |
| `librarian_add_root` | Añade carpeta a la configuración + reindexado automático |
| `librarian_list_roots` | Muestra las raíces configuradas y el estado del índice |
| `librarian_index` | Construye/reconstruye el índice de samples desde las carpetas |
| `librarian_analyze` | Analiza archivo: tono, BPM, tonalidad, duración |
| `librarian_analyze_folder` | Análisis por lotes de carpeta (ordenado por tono) |
| `librarian_recommend` | Recomendaciones compatibles por tonalidad Camelot Wheel |

### Integración opcional (requiere live-agent-remote)

| Herramienta | Descripción |
|------|-------------|
| `librarian_preview` | Importa sample como clip de audio en Ableton Live |
| `librarian_load_to_pad` | Carga sample en un pad de Drum Rack |

Las herramientas de integración detectan automáticamente si LiveAgent está en ejecución.
Si no está disponible, devuelven un error útil con instrucciones de configuración — las
herramientas principales siguen funcionando completamente.

## Uso con live-agent-remote

Estos dos proyectos son **independientes pero complementarios**:

| Proyecto | Rol |
|---------|------|
| **sample-librarian** | Buscar, analizar, recomendar samples |
| **live-agent-remote** | Controlar Ableton Live (MIDI, clips, dispositivos) |

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
0. librarian_add_root("~/Music/Ableton/User Library/Samples")  → register folder + auto index
1. librarian_recommend("Fm", category="Kick")     → get compatible kicks
2. librarian_preview("/path/to/kick.wav")          → preview in Ableton
3. librarian_load_to_pad("/path/to/kick.wav", ...) → load onto Drum Rack
4. mcp_liveagent_write_midi_notes(...)              → write drum pattern
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

| Tonalidad | Camelot | Compatible con |
|-----|---------|-----------------|
| Fm  | 4B      | Ebm(3B), C#m(5B), F(4A) |
| C   | 8A      | F(7A), G(9A), Am(8B) |

**Reglas:**
- Mismo número, misma letra (coincidencia perfecta)
- Número adyacente ±1, misma letra (transición suave)
- Mismo número, letra opuesta (relativo mayor/menor)
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
```

## Requisitos

- Python 3.10+
- librosa, numpy, scipy, soundfile (instalados automáticamente por setup.sh)
- mcp (para el servidor MCP)
- Opcional: [live-agent-remote](https://github.com/happytown-s/live-agent-remote) para la integración con Ableton

## Licencia

MIT
