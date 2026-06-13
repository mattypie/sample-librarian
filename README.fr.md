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

Recherchez, analysez et recommandez des samples audio depuis votre bibliothèque locale.
SQLite + FTS5 pour la recherche plein texte, enrichissement des résultats adapté aux
agents IA, détection des doublons et correspondance harmonique selon le Cercle Camelot.
Fonctionne de manière autonome ou s'intègre avec [live-agent-remote](https://github.com/happytown-s/live-agent-remote)
pour la prévisualisation dans Ableton Live et la création de Drum Racks one-shot.

## Fonctionnalités

- **Gestion des racines** — Ajoutez des dossiers de samples à la configuration avec ré-indexation automatique
- **Indexation** — Analysez les dossiers, extrayez les métadonnées, stockez dans SQLite avec FTS5
- **Recherche** — Recherche plein texte BM25 sur le nom, la catégorie, les tags et les chemins de dossiers
- **Résultats enrichis** — Chaque résultat de recherche inclut `key`, `bpm`, `pitch`, `confidence`, `recommended_use` et `ableton_action` — prêts à être exploités par des agents IA
- **Analyse** — Détection de hauteur (pitch), BPM, estimation de tonalité et analyse spectrale basées sur librosa
- **Détection de doublons** — Trouvez les doublons selon 4 axes : hachage de contenu, durée, hauteur et empreinte spectrale
- **Recommandation** — Correspondance harmonique selon le Cercle Camelot pour des samples compatibles en tonalité
- **Drum Rack one-shot** — `build_drum_rack_for_key()` recherche les samples compatibles, crée une piste Drum Rack, charge les kicks/snares/hi-hats sur les pads et écrit un pattern MIDI — le tout en un seul appel
- **Intégration Ableton** *(optionnel)* — Prévisualisez les samples dans Live, chargez-les sur les pads du Drum Rack, avec gestion de session TCP et groupes d'annulation

## Architecture

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

## Base de données

Sample Librarian utilise **SQLite avec FTS5** (recherche plein texte BM25) comme
index principal. Lors de la première exécution, les index JSONL existants sont
automatiquement migrés vers SQLite.

**Points clés du schéma :**
- `samples` — métadonnées des fichiers (chemin, nom, catégorie, taille, hachage)
- `analysis_cache` — hauteur, BPM, tonalité, durée, données spectrales
- `tags` — associations de tags consultables
- `roots` — dossiers de samples enregistrés avec historique d'analyse
- `samples_fts` — table virtuelle FTS5 pour la recherche BM25

**Enrichissement adapté aux IA** (`enrich_result()`) : chaque résultat de recherche est
enrichi avec :

| Champ | Description |
|-------|-------------|
| `key` | Tonalalité musicale détectée |
| `bpm` | Tempo détecté |
| `pitch` | Hauteur fondamentale (nom de la note + numéro) |
| `sample_type` | oneshot / short_loop / medium_loop / long_loop |
| `is_atonal` | Vrai pour les samples non harmoniques (hi-hats, bruit) |
| `confidence` | Score heuristique 0.0–1.0 basé sur la complétude de l'analyse |
| `recommended_use` | Comment utiliser ce sample (ex. « drum_kit_kick ») |
| `ableton_action` | Appel LiveAgent suggéré (ex. `load_sample_to_pad`) |
| `compatible_keys` | Tonalités compatibles harmoniquement selon le Cercle Camelot |

## Détection de doublons

Trouvez les samples redondants selon quatre axes indépendants :

| Fonction | Méthode | Cas d'usage |
|----------|---------|-------------|
| `find_duplicates_by_hash()` | Hachage de contenu (fichiers identiques) | Doublons exacts |
| `find_similar_by_duration()` | Durée dans une tolérance + même catégorie | Ré-exportations potentielles |
| `find_similar_by_pitch()` | Même classe de hauteur + même catégorie | Chevauchement tonal |
| `find_similar_by_spectral()` | Similarité du centroïde spectral | Même caractère/timbre |
| `find_all_duplicates()` | Toutes les méthodes ci-dessus combinées | Audit complet |

## Drum Rack one-shot (`build_drum_rack_for_key()`)

L'API d'orchestration qui relie tout :

```python
from librarian.live_agent_bridge import build_drum_rack_for_key

result = build_drum_rack_for_key(
    key="Fm",                    # tonalité cible
    track_index=-1,              # ajouter à la fin de la liste des pistes
    host="127.0.0.1",            # hôte LiveAgent
    port=8765,                   # port LiveAgent
)
```

Ce seul appel :
1. Recherche dans l'index SQLite des samples de kick, snare et hi-hat compatibles avec la tonalité
2. Crée une piste Drum Rack dans Ableton Live (par défaut : 808 Core Kit)
3. Charge les samples sur les pads 36 (kick), 38 (snare), 42 (closed hat)
4. Écrit optionnellement un pattern de batterie MIDI basique
5. Renvoie les chemins des samples chargés pour vérification

Voir `docs/recipes.md` pour une utilisation détaillée.

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

Voir `docs/mcp-clients.md` pour Claude Desktop, Cursor et autres clients.

## Outils MCP (9 au total)

### Noyau (toujours disponible)

- `librarian_search` — Rechercher dans l'index par mots-clés, catégorie, extension
- `librarian_add_root` — Ajouter un dossier à la configuration + ré-indexation automatique
- `librarian_list_roots` — Afficher les racines configurées et le statut de l'index
- `librarian_index` — Construire/reconstruire l'index des samples depuis les dossiers
- `librarian_analyze` — Analyser un fichier : hauteur (pitch), BPM, tonalité, durée
- `librarian_analyze_folder` — Analyser un dossier en lot (trié par hauteur)
- `librarian_recommend` — Recommandations de compatibilité harmonique selon le Cercle Camelot

### Intégration optionnelle (nécessite live-agent-remote)

- `librarian_preview` — Importer un sample comme clip audio dans Ableton Live
- `librarian_load_to_pad` — Charger un sample sur un pad du Drum Rack

Les outils d'intégration détectent automatiquement si LiveAgent est en cours d'exécution.
Si ce n'est pas disponible, ils renvoient une erreur utile avec des instructions
d'installation — les outils principaux restent entièrement fonctionnels.

## Utilisation avec live-agent-remote

Ces deux projets sont **indépendants mais complémentaires** :

- **sample-librarian** — Rechercher, analyser et recommander des samples
- **live-agent-remote** — Contrôler Ableton Live (MIDI, clips, appareils)

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
0. librarian_add_root("~/Music/Ableton/User Library/Samples")  → enregistrer + indexer
1. librarian_recommend("Fm", category="Kick")     → kicks compatibles
2. librarian_preview("/path/to/kick.wav")          → prévisualiser dans Ableton
3. librarian_load_to_pad("/path/to/kick.wav", ...) → charger sur le Drum Rack
4. mcp_liveagent_write_midi_notes(...)              → écrire un pattern de batterie

# Ou faites 1-4 en une seule fois :
build_drum_rack_for_key(key="Fm")  → recherche + création + chargement + MIDI
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

- Même numéro, même lettre — correspondance parfaite
- Numéro adjacent ±1, même lettre — transition fluide
- Même numéro, lettre opposée — relative majeure/mineure
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

# Base de données (détection de doublons, migration)
python3 -m librarian.db --duplicates
python3 -m librarian.db --migrate data/samples_index.jsonl
python3 -m librarian.db --stats
```

## Documentation

- `docs/recipes.md` — Flux de travail courants et exemples de code
- `docs/security.md` — Modèle de sécurité et opérations sûres
- `docs/troubleshooting.md` — Guide de débogage
- `docs/mcp-clients.md` — Configuration pour Claude Desktop, Cursor et autres

## Tests

```bash
.venv/bin/python3 -m pytest tests/ -v
```

22 tests couvrant les opérations de base de données, la recherche, l'analyse et l'enrichissement.
L'intégration continue (CI) s'exécute sur GitHub Actions avec ruff lint + pytest.

## Prérequis

- Python 3.10+
- librosa, numpy, scipy, soundfile (installés automatiquement par setup.sh)
- mcp (pour le serveur MCP)
- Optionnel : [live-agent-remote](https://github.com/happytown-s/live-agent-remote) pour l'intégration Ableton

## Licence

MIT
