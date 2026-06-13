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

Recherchez, analysez et recommandez des samples audio depuis votre bibliothèque locale.
Fonctionne de manière autonome ou s'intègre avec [live-agent-remote](https://github.com/happytown-s/live-agent-remote) pour la prévisualisation dans Ableton Live.

## Fonctionnalités

- **Gestion des racines** — Ajoutez des dossiers de samples à la configuration avec ré-indexation automatique
- **Indexation** — Analysez n'importe quel dossier de samples, extrayez les métadonnées (catégorie, tags, informations sur les fichiers)
- **Recherche** — Recherche par mots-clés avec score (nom > catégorie > tags > contenu)
- **Analyse** — Détection de hauteur (pitch), BPM et estimation de tonalité basés sur librosa
- **Recommandation** — Correspondance harmonique selon le Cercle Camelot pour des samples compatibles en tonalité
- **Intégration Ableton** *(optionnel)* — Prévisualisez les samples dans Live, chargez-les sur les pads du Drum Rack

## Architecture

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

**Les outils principaux fonctionnent sans Ableton.** Les outils d'intégration détectent
automatiquement si LiveAgent est en cours d'exécution et fournissent des messages
d'installation utiles si ce n'est pas le cas.

## Démarrage rapide

```bash
# Installation
git clone https://github.com/happytown-s/sample-librarian.git
cd sample-librarian
bash setup.sh

# Ajouter des dossiers de samples (indexation automatique lors de l'ajout)
.venv/bin/python3 -c "from mcp_server import librarian_add_root; print(librarian_add_root('~/Music/Ableton/User Library/Samples'))"

# Ou construire l'index manuellement
.venv/bin/python3 -m librarian.index --root ~/path/to/samples

# Recherche
.venv/bin/python3 -m librarian.search dark bass

# Recommander des samples compatibles en tonalité
.venv/bin/python3 -m librarian.recommend Fm kick --analyze
```

## Serveur MCP (pour agents IA)

### Hermes Agent

Ajoutez à `~/.hermes/profiles/<profile>/config.yaml` :

```yaml
mcp_servers:
  librarian:
    command: /path/to/sample-librarian/.venv/bin/python3
    args: [/path/to/sample-librarian/mcp_server.py]
```

### Autres clients MCP

Pointez votre client MCP vers :
```
Command: /path/to/sample-librarian/.venv/bin/python3
Args: [/path/to/sample-librarian/mcp_server.py]
```

## Outils MCP (9 au total)

### Noyau (toujours disponible)

| Outil | Description |
|-------|-------------|
| `librarian_search` | Rechercher dans l'index par mots-clés, catégorie, extension |
| `librarian_add_root` | Ajouter un dossier à la configuration + ré-indexation automatique |
| `librarian_list_roots` | Afficher les racines configurées et le statut de l'index |
| `librarian_index` | Construire/reconstruire l'index des samples depuis les dossiers |
| `librarian_analyze` | Analyser un fichier : hauteur (pitch), BPM, tonalité, durée |
| `librarian_analyze_folder` | Analyser un dossier en lot (trié par hauteur) |
| `librarian_recommend` | Recommandations de compatibilité harmonique selon le Cercle Camelot |

### Intégration optionnelle (nécessite live-agent-remote)

| Outil | Description |
|-------|-------------|
| `librarian_preview` | Importer un sample comme clip audio dans Ableton Live |
| `librarian_load_to_pad` | Charger un sample sur un pad du Drum Rack |

Les outils d'intégration détectent automatiquement si LiveAgent est en cours d'exécution.
Si ce n'est pas disponible, ils renvoient une erreur utile avec des instructions
d'installation — les outils principaux restent entièrement fonctionnels.

## Utilisation avec live-agent-remote

Ces deux projets sont **indépendants mais complémentaires** :

| Projet | Rôle |
|--------|------|
| **sample-librarian** | Rechercher, analyser et recommander des samples |
| **live-agent-remote** | Contrôler Ableton Live (MIDI, clips, appareils) |

Enregistrez les deux serveurs MCP dans la configuration de votre agent IA :

```yaml
mcp_servers:
  liveagent:
    command: /path/to/live-agent-remote/.venv/bin/python3
    args: [/path/to/live-agent-remote/mcp_server.py]
  librarian:
    command: /path/to/sample-librarian/.venv/bin/python3
    args: [/path/to/sample-librarian/mcp_server.py]
```

### Flux de travail type

```
0. librarian_add_root("~/Music/Ableton/User Library/Samples")  → enregistrer le dossier + indexation auto
1. librarian_recommend("Fm", category="Kick")     → obtenir les kicks compatibles
2. librarian_preview("/path/to/kick.wav")          → prévisualiser dans Ableton
3. librarian_load_to_pad("/path/to/kick.wav", ...) → charger sur le Drum Rack
4. mcp_liveagent_write_midi_notes(...)              → écrire un pattern de batterie
```

## Configuration

Modifiez `config.local.py` (ignoré par git) :

```python
# Requis : dossiers de samples à indexer
SAMPLES_ROOTS = [
    "~/Music/Ableton/User Library/Samples",
    "/path/to/your/sample/library",
]

# Optionnel : intégration LiveAgent
LIVEAGENT_HOST = "127.0.0.1"
LIVEAGENT_PORT = 8765
```

Ou utilisez des variables d'environnement :

```bash
export SAMPLES_PATH="/path/to/samples"
export LIVEAGENT_HOST=127.0.0.1
export LIVEAGENT_PORT=8765
```

## Correspondance harmonique du Cercle Camelot

Les recommandations utilisent le système du Cercle Camelot :

| Tonalité | Camelot | Compatible avec |
|----------|---------|-----------------|
| Fm       | 4B      | Ebm(3B), C#m(5B), F(4A) |
| C        | 8A      | F(7A), G(9A), Am(8B) |

**Règles :**
- Même numéro, même lettre (correspondance parfaite)
- Numéro adjacent ±1, même lettre (transition fluide)
- Même numéro, lettre opposée (relative majeure/mineure)
- Les samples atonaux (hi-hats, bruit) sont toujours inclus

## Utilisation en ligne de commande

```bash
# Indexation
python3 -m librarian.index --root ~/samples --root ~/more/samples
python3 -m librarian.index --query bass --query dark

# Recherche
python3 -m librarian.search dark bass --limit 10
python3 -m librarian.search 808 kick --category Kick --json

# Analyse
python3 -m librarian.analyze file.wav --mode full
python3 -m librarian.analyze ./folder/ --mode pitch

# Recommandation
python3 -m librarian.recommend Fm kick --analyze
python3 -m librarian.recommend C --category Bass
```

## Prérequis

- Python 3.10+
- librosa, numpy, scipy, soundfile (installés automatiquement par setup.sh)
- mcp (pour le serveur MCP)
- Optionnel : [live-agent-remote](https://github.com/happytown-s/live-agent-remote) pour l'intégration Ableton

## Licence

MIT
