"""MCP Server for Sample Librarian.

Exposes 9 tools for AI agents to search, analyze, and recommend samples.
Optionally integrates with live-agent-remote for Ableton Live preview.

Core tools (always available):
  - librarian_search       — Search sample index by keywords
  - librarian_index        — Build/rebuild the sample index
  - librarian_add_root     — Add a folder to config + auto re-index
  - librarian_list_roots   — Show configured roots and index status
  - librarian_analyze      — Analyze audio file (pitch, BPM, key)
  - librarian_analyze_folder — Batch analyze a folder
  - librarian_recommend    — Recommend key-compatible samples

Integration tools (require live-agent-remote running):
  - librarian_preview       — Preview sample in Ableton Live
  - librarian_load_to_pad   — Load sample onto Drum Rack pad
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Ensure we can import the librarian package
sys.path.insert(0, str(Path(__file__).parent))

try:
    from mcp.server.fastmcp import FastMCP
    HAS_MCP = True
except ImportError:
    HAS_MCP = False

from librarian.analyze import analyze_file, analyze_folder, get_compatible_keys
from librarian.db import (
    get_db,
    get_stats,
    recommend_samples_db,
    scan_root_to_db,
    search_samples_enriched,
)

# Check LiveAgent availability lazily
_LIVEAGENT_CHECKED = False
_LIVEAGENT_AVAILABLE = False


def _check_liveagent() -> bool:
    global _LIVEAGENT_CHECKED, _LIVEAGENT_AVAILABLE
    if _LIVEAGENT_CHECKED:
        return _LIVEAGENT_AVAILABLE
    _LIVEAGENT_CHECKED = True
    try:
        from librarian.live_agent_bridge import is_available
        _LIVEAGENT_AVAILABLE = is_available()
    except Exception:
        _LIVEAGENT_AVAILABLE = False
    return _LIVEAGENT_AVAILABLE


def _get_db_path() -> str:
    """Get the SQLite database path from config or default."""
    try:
        from config import get_db_path
        return get_db_path()
    except Exception:
        return str(Path(__file__).parent / "data" / "samples.db")


# Schema initialization runs once at startup, not on every tool call.
# Running init_db per-call caused redundant DDL under concurrent requests
# and risked "database is locked" on parallel tool invocations.
_db_initialized = False


def _get_db():
    """Open a DB connection for a single tool call. Caller must close().

    Ensures the schema exists on first call only (idempotent thereafter).
    """
    global _db_initialized
    db_path = _get_db_path()
    if not _db_initialized:
        from librarian.db import init_db
        init_db(db_path)
        _db_initialized = True
    return get_db(db_path)


if not HAS_MCP:
    print("MCP package not installed. Run: pip install mcp", file=sys.stderr)
    sys.exit(1)

mcp = FastMCP("sample-librarian")


# ─── Core Tools ───

@mcp.tool()
def librarian_search(
    terms: list[str],
    limit: int = 20,
    category: str = "",
    ext: str = "",
) -> str:
    """Search the sample index by keywords.

    Args:
        terms: Search terms (all must match, AND logic)
        limit: Max results (default 20)
        category: Filter by category (e.g., "Kick", "Snare")
        ext: Filter by extension (e.g., ".wav")

    Returns:
        JSON array of matching samples with name, path, category, tags.
    """
    query = " ".join(terms) if terms else ""
    conn = _get_db()
    try:
        results = search_samples_enriched(
            conn, query,
            category=category or None,
            limit=limit,
        )
        if ext:
            results = [r for r in results if r.get("ext", "").lstrip(".").lower()
                       == ext.lstrip(".").lower()]
    finally:
        conn.close()
    return json.dumps(results, ensure_ascii=False)


@mcp.tool()
def librarian_index(
    roots: list[str],
    scan_presets: bool = True,
) -> str:
    """Build or rebuild the sample index from folder(s).

    Args:
        roots: List of root folders to scan
        scan_presets: Include preset files (.nmsv, .nksf, etc.)

    Returns:
        JSON summary with total files, categories, sizes.
    """
    conn = _get_db()
    try:
        summaries = []
        total_new = 0
        for root in roots:
            r = scan_root_to_db(conn, root, scan_presets=scan_presets)
            summaries.append(r)
            total_new += r["files_new"]

        stats = get_stats(conn)
        summary = {
            "total_files": stats["total_samples"],
            "total_new": total_new,
            "roots_scanned": summaries,
            "categories": stats["by_category"],
            "analyzed_count": stats["analyzed_count"],
        }
    finally:
        conn.close()
    return json.dumps(summary, ensure_ascii=False)


@mcp.tool()
def librarian_add_root(
    path: str,
    rebuild_index: bool = True,
) -> str:
    """Add a sample folder to config.local.py and optionally re-index.

    Persists the path so it's included in all future searches and recommendations.
    If config.local.py doesn't exist yet, creates it from the template.

    Args:
        path: Absolute or ~ path to the folder to add
        rebuild_index: If True (default), immediately scan all configured roots

    Returns:
        JSON with updated roots list and index summary (if rebuilt).
    """
    base_dir = Path(__file__).parent
    local_path = base_dir / "config.local.py"

    # Normalize the input path
    clean_path = os.path.expanduser(path)
    if not os.path.isabs(clean_path):
        clean_path = os.path.abspath(clean_path)

    # Validate the path exists
    if not os.path.isdir(clean_path):
        return json.dumps({"error": f"Directory not found: {clean_path}"})

    # Load current SAMPLES_ROOTS from config
    from config import get_samples_roots
    current_roots = get_samples_roots()

    # Check for duplicate
    already = any(os.path.expanduser(r) == clean_path for r in current_roots)
    if already:
        return json.dumps({
            "status": "already_exists",
            "roots": current_roots,
            "message": f"Path already in config: {clean_path}",
        })

    # Read or create config.local.py
    if local_path.exists():
        content = local_path.read_text(encoding="utf-8")
    else:
        # Create from template
        template = (base_dir / "config.example.py").read_text(encoding="utf-8")
        content = template

    # Add the new path to SAMPLES_ROOTS
    # Strategy: find the SAMPLES_ROOTS list and append
    lines = content.split("\n")
    new_lines = []
    in_roots = False
    roots_end_idx = None
    for i, line in enumerate(lines):
        if "SAMPLES_ROOTS" in line and "=" in line and "[" in line:
            in_roots = True
        if in_roots and "]" in line:
            roots_end_idx = i
            in_roots = False
        new_lines.append(line)

    # Insert before the closing ]
    if roots_end_idx is not None:
        new_lines.insert(roots_end_idx, f'    "{clean_path}",')
    else:
        # No SAMPLES_ROOTS found — append the whole block
        new_lines.append("")
        new_lines.append("SAMPLES_ROOTS = [")
        new_lines.append(f'    "{clean_path}",')
        new_lines.append("]")

    local_path.write_text("\n".join(new_lines), encoding="utf-8")

    result = {
        "status": "added",
        "path": clean_path,
        "config_file": str(local_path),
    }

    # Re-index if requested
    if rebuild_index:
        all_roots = current_roots + [clean_path]
        conn = _get_db()
        try:
            scan_results = []
            for r in all_roots:
                scan_results.append(scan_root_to_db(conn, r, scan_presets=True))
            stats = get_stats(conn)
        finally:
            conn.close()

        result["index_summary"] = {
            "total_files": stats["total_samples"],
            "roots_scanned": scan_results,
            "categories": stats["by_category"],
        }
        result["total_roots"] = len(all_roots)

    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def librarian_list_roots() -> str:
    """Show all configured sample folders and current index status.

    Returns:
        JSON with:
        - roots: list of configured folder paths with on-disk status
        - index: DB summary (total samples, analyzed count, last scan)
    """
    from config import get_samples_roots
    roots = get_samples_roots()

    # On-disk status per root
    root_status = []
    for r in roots:
        expanded = os.path.expanduser(r)
        exists = os.path.isdir(expanded)
        file_count = 0
        if exists:
            for ext in (".wav", ".aiff", ".aif", ".mp3", ".ogg", ".flac"):
                file_count += sum(
                    1 for _ in Path(expanded).rglob(f"*{ext}")
                )
        root_status.append({
            "path": r,
            "exists": exists,
            "audio_files": file_count,
        })

    # DB stats instead of JSONL line count
    conn = _get_db()
    try:
        stats = get_stats(conn)
        # Last scan timestamp from scan_history
        last_scan_row = conn.execute(
            "SELECT completed_at FROM scan_history "
            "ORDER BY completed_at DESC LIMIT 1"
        ).fetchone()
        last_scan = last_scan_row[0] if last_scan_row else None
    finally:
        conn.close()

    return json.dumps({
        "roots": root_status,
        "index": {
            "exists": stats["total_samples"] > 0,
            "indexed_files": stats["total_samples"],
            "analyzed_count": stats["analyzed_count"],
            "last_built": last_scan,
            "by_category": stats["by_category"],
        },
    }, ensure_ascii=False)


@mcp.tool()
def librarian_analyze(
    file_path: str,
    mode: str = "full",
) -> str:
    """Analyze a single audio file.

    Args:
        file_path: Path to audio file
        mode: 'full' (BPM + key + pitch), 'pitch' (pitch only), 'bpm' (BPM only)

    Returns:
        JSON with pitch, BPM, key, duration, sample_type.
    """
    result = analyze_file(file_path, mode=mode)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def librarian_analyze_folder(
    folder_path: str,
    mode: str = "pitch",
    recursive: bool = True,
) -> str:
    """Analyze all audio files in a folder.

    Args:
        folder_path: Path to folder
        mode: 'full', 'pitch', or 'bpm'
        recursive: Scan subdirectories

    Returns:
        JSON array of analysis results, sorted by pitch.
    """
    results = analyze_folder(folder_path, mode=mode, recursive=recursive)
    return json.dumps(results, ensure_ascii=False)


@mcp.tool()
def librarian_recommend(
    target_key: str,
    terms: list[str] | None = None,
    category: str = "",
    limit: int = 20,
    analyze: bool = False,
) -> str:
    """Recommend samples harmonically compatible with a target key.

    Uses Camelot Wheel matching (adjacent keys ±1, relative major/minor).

    Args:
        target_key: Target key (e.g., "Fm", "C", "Am")
        terms: Optional search terms to filter
        category: Filter by category
        limit: Max results
        analyze: Reserved (analysis comes from cache, not real-time)

    Returns:
        JSON array of recommended samples.
    """
    conn = _get_db()
    try:
        results = recommend_samples_db(
            conn,
            target_key=target_key,
            terms=terms,
            category=category or None,
            limit=limit,
        )
    finally:
        conn.close()
    compatible = get_compatible_keys(target_key)
    output = {
        "target_key": target_key,
        "compatible_keys": compatible,
        "results": results,
    }
    return json.dumps(output, ensure_ascii=False)


# ─── LiveAgent Integration Tools (optional) ───

@mcp.tool()
def librarian_preview(
    file_path: str,
    track_index: int = -1,
    slot_index: int = -1,
) -> str:
    """Preview a sample in Ableton Live.

    Requires live-agent-remote running with Ableton Live open.
    Creates an audio clip on the specified (or auto-assigned) track/slot.

    Args:
        file_path: Path to audio sample
        track_index: Target track (-1 = auto-assign audio track)
        slot_index: Target clip slot (-1 = auto-assign next empty)

    Returns:
        JSON result from LiveAgent.
    """
    if not _check_liveagent():
        return json.dumps({
            "error": (
                "LiveAgent not available. Install live-agent-remote "
                "and ensure Ableton Live is running with LiveAgent active."
            )
        })
    from librarian.live_agent_bridge import preview_sample
    result = preview_sample(file_path, track_index, slot_index)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def librarian_load_to_pad(
    file_path: str,
    track_index: int,
    pad_index: int,
    drum_rack_index: int = 0,
    reset_effects: bool = False,
) -> str:
    """Load a sample onto a Drum Rack pad in Ableton Live.

    Requires live-agent-remote with a preset Drum Kit loaded.
    Uses hotswap_target technique to swap samples without destroying the rack.

    Args:
        file_path: Path to audio sample
        track_index: Track containing the Drum Rack
        pad_index: MIDI note number for the pad (36=C1 kick, 38=snare)
        drum_rack_index: Device index of Drum Rack (default 0)
        reset_effects: Clear effects chain after loading

    Returns:
        JSON result from LiveAgent.
    """
    if not _check_liveagent():
        return json.dumps({
            "error": (
                "LiveAgent not available. Install live-agent-remote "
                "and ensure Ableton Live is running with LiveAgent active."
            )
        })
    from librarian.live_agent_bridge import load_to_drum_pad
    result = load_to_drum_pad(
        file_path, track_index, pad_index,
        drum_rack_index=drum_rack_index,
        reset_effects=reset_effects,
    )
    return json.dumps(result, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
