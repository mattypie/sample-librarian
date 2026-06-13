"""Optional LiveAgent bridge — connects to live-agent-remote via TCP.

This module is OPTIONAL. It enables:
- Preview samples in Ableton Live (import audio clip)
- Load samples onto Drum Rack pads

If LiveAgent is not running or not configured, all functions raise
RuntimeError with a helpful message. The core librarian functionality
(search, analyze, recommend) works WITHOUT this module.

Setup:
1. Install live-agent-remote: https://github.com/happytown-s/live-agent-remote
2. Ensure Ableton Live is running with LiveAgent Control Surface active
3. Set LIVEAGENT_HOST and LIVEAGENT_PORT in config.local.py
"""

from __future__ import annotations

import json
import socket
from typing import Any, Optional

try:
    from .config import get_liveagent_host, get_liveagent_port
    _DEFAULT_HOST = get_liveagent_host()
    _DEFAULT_PORT = get_liveagent_port()
except Exception:
    _DEFAULT_HOST = "127.0.0.1"
    _DEFAULT_PORT = 8765


class LiveAgentNotAvailable(RuntimeError):
    """Raised when LiveAgent is not reachable."""

    def __init__(self, detail: str = ""):
        msg = (
            "LiveAgent is not available. "
            "To enable Ableton integration:\n"
            "1. Install live-agent-remote (https://github.com/happytown-s/live-agent-remote)\n"
            "2. Open Ableton Live with LiveAgent as Control Surface\n"
            "3. Set LIVEAGENT_HOST/LIVEAGENT_PORT in config.local.py"
        )
        if detail:
            msg += f"\nDetail: {detail}"
        super().__init__(msg)


def _send(
    command: str,
    payload: Optional[dict[str, Any]] = None,
    host: str = "",
    port: int = 0,
    timeout: int = 10,
) -> dict[str, Any]:
    """Send a command to LiveAgent via TCP."""
    host = host or _DEFAULT_HOST
    port = port or _DEFAULT_PORT
    payload = payload or {}
    payload["command"] = command

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        sock.sendall((json.dumps(payload) + "\n").encode())
        data = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
            if b"\n" in data:
                break
        return json.loads(data.decode().strip())
    except (ConnectionRefusedError, socket.timeout, OSError) as e:
        raise LiveAgentNotAvailable(str(e))
    finally:
        sock.close()


def is_available(host: str = "", port: int = 0) -> bool:
    """Check if LiveAgent is reachable."""
    try:
        result = _send("ping", host=host, port=port, timeout=3)
        return result.get("ok", False) or result.get("result") == "pong"
    except LiveAgentNotAvailable:
        return False


def preview_sample(
    file_path: str,
    track_index: int = -1,
    slot_index: int = -1,
    host: str = "",
    port: int = 0,
) -> dict[str, Any]:
    """Import a sample into Ableton Live for preview.

    Creates an audio clip on the specified track/slot (or auto-assigned).
    """
    # Auto-assign track if not specified
    if track_index < 0:
        state = _send("get_live_state", host=host, port=port)
        tracks = state.get("tracks", [])
        # Find or create an audio track
        audio_tracks = [
            i for i, t in enumerate(tracks)
            if t.get("type") == "audio"
        ]
        if audio_tracks:
            track_index = audio_tracks[-1]
        else:
            _send("create_audio_track", {"index": -1}, host, port)
            state = _send("get_live_state", host=host, port=port)
            track_index = len(state.get("tracks", [])) - 1

    if slot_index < 0:
        # Find next empty slot
        state = _send("get_live_state", host=host, port=port)
        tracks = state.get("tracks", [])
        if track_index < len(tracks):
            clips = tracks[track_index].get("clip_slots", [])
            slot_index = len(clips)
            for i, c in enumerate(clips):
                if not c.get("has_clip"):
                    slot_index = i
                    break

    return _send(
        "import_audio_clip",
        {
            "track_index": track_index,
            "slot_index": slot_index,
            "file_path": file_path,
        },
        host, port,
    )


def load_to_drum_pad(
    file_path: str,
    track_index: int,
    pad_index: int,
    drum_rack_index: int = 0,
    reset_effects: bool = False,
    host: str = "",
    port: int = 0,
) -> dict[str, Any]:
    """Load a sample onto a Drum Rack pad.

    Requires a preset kit with existing chains on the target pad.
    """
    return _send(
        "load_sample_to_pad",
        {
            "track_index": track_index,
            "pad_index": pad_index,
            "file_path": file_path,
            "drum_rack_index": drum_rack_index,
            "reset_effects": reset_effects,
        },
        host, port,
    )


# ─────────────────────────────────────────────────────────────
# High-level orchestration: build_drum_rack_for_key()
# ─────────────────────────────────────────────────────────────

# Standard MIDI drum pad layout
_PAD_MAP = {
    "kick": 36,   # C1
    "snare": 38,  # D1
    "clap": 39,   # D#1
    "closed_hat": 42,  # F#1
    "open_hat": 46,    # A#1
    "tom": 45,    # A1
    "rim": 37,    # C#1
}

# 2-step / garage pattern templates (16th notes, 1 bar = 4 beats)
_PATTERN_2STEP = [
    # Beat 1: kick on 1
    {"pitch": 36, "start": 0.0, "duration": 0.25, "velocity": 110},
    # Beat 2: snare on 2, kick after
    {"pitch": 38, "start": 1.0, "duration": 0.25, "velocity": 100},
    {"pitch": 36, "start": 1.5, "duration": 0.25, "velocity": 90},
    # Beat 3: kick on 3
    {"pitch": 36, "start": 2.0, "duration": 0.25, "velocity": 105},
    # Beat 4: snare on 4
    {"pitch": 38, "start": 3.0, "duration": 0.25, "velocity": 100},
    # Hats: off-beat 16th shuffles
    {"pitch": 42, "start": 0.5, "duration": 0.125, "velocity": 60},
    {"pitch": 42, "start": 1.25, "duration": 0.125, "velocity": 55},
    {"pitch": 42, "start": 1.75, "duration": 0.125, "velocity": 70},
    {"pitch": 42, "start": 2.5, "duration": 0.125, "velocity": 60},
    {"pitch": 42, "start": 3.25, "duration": 0.125, "velocity": 55},
    {"pitch": 42, "start": 3.75, "duration": 0.125, "velocity": 65},
]

_PATTERN_4FLOOR = [
    {"pitch": 36, "start": 0.0, "duration": 0.25, "velocity": 110},
    {"pitch": 36, "start": 1.0, "duration": 0.25, "velocity": 110},
    {"pitch": 36, "start": 2.0, "duration": 0.25, "velocity": 110},
    {"pitch": 36, "start": 3.0, "duration": 0.25, "velocity": 110},
    {"pitch": 38, "start": 1.0, "duration": 0.25, "velocity": 95},
    {"pitch": 38, "start": 3.0, "duration": 0.25, "velocity": 95},
    {"pitch": 42, "start": 0.5, "duration": 0.25, "velocity": 70},
    {"pitch": 42, "start": 1.5, "duration": 0.25, "velocity": 70},
    {"pitch": 42, "start": 2.5, "duration": 0.25, "velocity": 70},
    {"pitch": 42, "start": 3.5, "duration": 0.25, "velocity": 70},
    {"pitch": 46, "start": 0.5, "duration": 0.125, "velocity": 50},
    {"pitch": 46, "start": 2.5, "duration": 0.125, "velocity": 50},
]

_PATTERN_TRAP = [
    {"pitch": 36, "start": 0.0, "duration": 0.25, "velocity": 115},
    {"pitch": 36, "start": 1.75, "duration": 0.25, "velocity": 100},
    {"pitch": 36, "start": 2.5, "duration": 0.25, "velocity": 90},
    {"pitch": 38, "start": 1.0, "duration": 0.25, "velocity": 105},
    {"pitch": 38, "start": 3.0, "duration": 0.25, "velocity": 105},
    {"pitch": 42, "start": 0.0, "duration": 0.125, "velocity": 60},
    {"pitch": 42, "start": 0.25, "duration": 0.125, "velocity": 55},
    {"pitch": 42, "start": 0.5, "duration": 0.125, "velocity": 60},
    {"pitch": 42, "start": 0.75, "duration": 0.125, "velocity": 55},
    {"pitch": 42, "start": 1.0, "duration": 0.125, "velocity": 60},
    {"pitch": 42, "start": 1.25, "duration": 0.125, "velocity": 55},
    {"pitch": 42, "start": 1.5, "duration": 0.125, "velocity": 60},
    {"pitch": 42, "start": 1.75, "duration": 0.125, "velocity": 55},
    {"pitch": 42, "start": 2.0, "duration": 0.125, "velocity": 60},
    {"pitch": 42, "start": 2.25, "duration": 0.125, "velocity": 55},
    {"pitch": 42, "start": 2.5, "duration": 0.125, "velocity": 60},
    {"pitch": 42, "start": 2.75, "duration": 0.125, "velocity": 55},
    {"pitch": 42, "start": 3.0, "duration": 0.125, "velocity": 60},
    {"pitch": 42, "start": 3.25, "duration": 0.125, "velocity": 55},
    {"pitch": 42, "start": 3.5, "duration": 0.125, "velocity": 60},
    {"pitch": 42, "start": 3.75, "duration": 0.125, "velocity": 65},
]

PATTERNS = {
    "2step": _PATTERN_2STEP,
    "4floor": _PATTERN_4FLOOR,
    "trap": _PATTERN_TRAP,
}


def build_drum_rack_for_key(
    target_key: str,
    *,
    db_path: str = "",
    track_index: int = -1,
    pattern: str = "2step",
    kit_name: str = "808 Core Kit.adg",
    create_clip: bool = True,
    slot_index: int = -1,
    limit_per_category: int = 3,
    host: str = "",
    port: int = 0,
) -> dict[str, Any]:
    """One-shot: search → Drum Rack → load samples → MIDI pattern.

    Workflow:
    1. Search Sample Librarian DB for Kick/Snare/Hat compatible with target_key
    2. Create a Drum Rack track in Ableton Live (or use existing)
    3. Load best-matching samples onto pads (36=kick, 38=snare, 42=hat)
    4. Create a MIDI clip with the selected pattern

    Parameters
    ----------
    target_key:
        Target musical key (e.g. "Fm", "C", "Am") for harmonic matching.
    db_path:
        Path to samples.db. Defaults to data/samples.db.
    track_index:
        Track to use. -1 = create new Drum Rack at end.
    pattern:
        Pattern style: "2step", "4floor", or "trap".
    kit_name:
        Drum Rack preset name from Ableton's Drums browser.
    create_clip:
        If True, create a MIDI clip with the pattern.
    slot_index:
        Slot for the MIDI clip. -1 = next empty slot.
    limit_per_category:
        Max samples to consider per category (kick/snare/hat).

    Returns
    -------
    dict
        Summary: target_key, compatible_keys, track_index, loaded_samples,
        pattern, clip_info.
    """
    from .db import get_db, search_samples_enriched, close_db, DEFAULT_DB_PATH
    from .analyze import get_compatible_keys

    host = host or _DEFAULT_HOST
    port = port or _DEFAULT_PORT

    result: dict[str, Any] = {
        "target_key": target_key,
        "compatible_keys": get_compatible_keys(target_key),
        "pattern": pattern,
    }

    # ── Step 1: Search DB ──
    db_path = db_path or DEFAULT_DB_PATH
    conn = get_db(db_path)

    searches = {}
    for cat, search_terms in [
        ("Kick", f"kick {target_key.rstrip('m')}"),
        ("Snare", "snare"),
        ("HiHat", "hihat closed"),
    ]:
        hits = search_samples_enriched(
            conn, search_terms, category=cat, limit=limit_per_category,
        )
        searches[cat] = hits

    close_db(conn)
    result["candidates"] = {
        cat: [{"name": h["name"], "path": h["path"], "pitch": h.get("pitch"),
               "key": h.get("key"), "confidence": h.get("confidence")}
              for h in hits]
        for cat, hits in searches.items()
    }

    # Pick best sample per category (first result)
    selected: dict[str, dict] = {}
    for cat, hits in searches.items():
        if hits:
            selected[cat] = hits[0]

    if not selected.get("Kick"):
        result["error"] = "No kick samples found in library"
        return result

    # ── Step 2: Create / locate Drum Rack track ──
    if track_index < 0:
        rack_result = _send(
            "create_drum_rack",
            {"track_index": -1, "name": f"Drum Rack ({target_key})", "kit_name": kit_name},
            host, port,
        )
        # Get track index from result
        state = _send("get_live_state", host=host, port=port)
        tracks = state.get("tracks", [])
        track_index = len(tracks) - 1
        result["drum_rack_created"] = True
    else:
        result["drum_rack_created"] = False

    result["track_index"] = track_index

    # ── Step 3: Load samples onto pads ──
    loaded = []
    pad_map = {
        "Kick": _PAD_MAP["kick"],
        "Snare": _PAD_MAP["snare"],
        "HiHat": _PAD_MAP["closed_hat"],
    }

    for cat, sample in selected.items():
        pad = pad_map.get(cat)
        if pad is None:
            continue
        load_result = _send(
            "load_sample_to_pad",
            {
                "track_index": track_index,
                "pad_index": pad,
                "file_path": sample["path"],
                "reset_effects": False,
            },
            host, port,
        )
        loaded.append({
            "category": cat,
            "name": sample["name"],
            "pad": pad,
            "path": sample["path"],
            "success": load_result.get("ok", False),
        })

    result["loaded_samples"] = loaded

    # ── Step 4: Create MIDI clip with pattern ──
    if create_clip:
        notes = PATTERNS.get(pattern, _PATTERN_2STEP)

        if slot_index < 0:
            state = _send("get_live_state", host=host, port=port)
            tracks = state.get("tracks", [])
            if track_index < len(tracks):
                clips = tracks[track_index].get("clip_slots", [])
                slot_index = len(clips)
                for i, c in enumerate(clips):
                    if not c.get("has_clip"):
                        slot_index = i
                        break
            else:
                slot_index = 0

        _send(
            "create_session_clip",
            {"track_index": track_index, "slot_index": slot_index,
             "length_beats": 4, "name": f"{pattern} ({target_key})"},
            host, port,
        )

        clip_result = _send(
            "write_midi_notes",
            {"track_index": track_index, "slot_index": slot_index, "notes": notes},
            host, port,
        )

        result["clip_info"] = {
            "track_index": track_index,
            "slot_index": slot_index,
            "pattern": pattern,
            "note_count": len(notes),
            "success": clip_result.get("ok", False),
        }

    return result
