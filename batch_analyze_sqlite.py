#!/usr/bin/env python3
"""Batch scan + analyze Battery 4 + Maschine 2 Factory directly into SQLite."""

import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from librarian.analyze import analyze_file
from librarian.db import get_db, init_db, upsert_analysis, upsert_sample

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "samples.db")

LIBS = [
    "/Volumes/SubSSD/Komplete Library/Battery 4 Factory Library",
    "/Volumes/SubSSD/Komplete Library/Maschine 2 Factory Library Library",
]
EXTENSIONS = {'.wav', '.aif', '.aiff', '.mp3', '.flac', '.ogg', '.m4a'}


def collect_files():
    files = []
    for lib in LIBS:
        if not os.path.isdir(lib):
            print(f"WARNING: {lib} not found, skipping", flush=True)
            continue
        count = 0
        for root, _dirs, fnames in os.walk(lib):
            for f in fnames:
                ext = os.path.splitext(f)[1].lower()
                if ext in EXTENSIONS:
                    files.append(os.path.join(root, f))
                    count += 1
        print(f"  {os.path.basename(lib)}: {count} files", flush=True)
    return files


def file_hash(path):
    """Quick hash of file path + size for dedup."""
    try:
        st = os.stat(path)
        return hashlib.md5(f"{path}:{st.st_size}".encode()).hexdigest()
    except OSError:
        return None


def derive_category(path):
    """Derive category from folder structure."""
    parts = path.split(os.sep)
    for p in parts:
        pl = p.lower()
        if pl in ("kick", "kicks"):
            return "Kick"
        elif pl in ("snare", "snares"):
            return "Snare"
        elif pl in ("clap", "claps"):
            return "Clap"
        elif "hat" in pl or "hihat" in pl:
            return "Hat"
        elif pl in ("tom", "toms"):
            return "Tom"
        elif pl in ("cymbal", "cymbals", "crash", "ride"):
            return "Cymbal"
        elif pl in ("percussion", "perc"):
            return "Percussion"
        elif "fx" in pl or "effect" in pl:
            return "FX"
        elif pl in ("loop", "loops"):
            return "Loop"
        elif pl in ("bass", "sub"):
            return "Bass"
        elif pl in ("synth", "lead", "pad"):
            return "Synth"
        elif pl in ("vocal", "vox"):
            return "Vocal"
    return ""


def run():
    print(f"DB: {DB_PATH}", flush=True)
    init_db(DB_PATH)
    conn = get_db(DB_PATH)

    print("Collecting files...", flush=True)
    files = collect_files()
    total = len(files)
    print(f"Total: {total} files", flush=True)

    errors = 0
    analyzed = 0
    skipped = 0
    t_start = time.time()

    for i, fp in enumerate(files):
        # Check if already analyzed in DB
        existing = conn.execute(
            "SELECT s.id, a.sample_id FROM samples s "
            "LEFT JOIN analysis_cache a ON s.id = a.sample_id "
            "WHERE s.path = ?", (fp,)
        ).fetchone()

        if existing and existing[1] is not None:
            skipped += 1
        else:
            try:
                # Insert/update sample record
                name = os.path.basename(fp)
                ext = os.path.splitext(fp)[1].lower().lstrip('.')
                try:
                    size = os.path.getsize(fp)
                except OSError:
                    size = 0
                category = derive_category(fp)
                folder = os.path.basename(os.path.dirname(fp))

                record = {
                    "path": fp,
                    "name": name,
                    "ext": ext,
                    "size": size,
                    "category": category,
                    "folder": folder,
                    "root": "SubSSD",
                    "file_hash": file_hash(fp),
                    "strings": [],
                    "tags": [],
                }
                sample_id = upsert_sample(conn, record)

                # Check if analysis already exists for this sample_id
                has_analysis = conn.execute(
                    "SELECT 1 FROM analysis_cache WHERE sample_id = ?", (sample_id,)
                ).fetchone()

                if not has_analysis:
                    result = analyze_file(fp, mode="full")
                    if "error" not in result:
                        upsert_analysis(conn, sample_id, result)
                        analyzed += 1
                    else:
                        errors += 1
                else:
                    skipped += 1

            except Exception:
                errors += 1

        # Progress every 100 files
        if (i + 1) % 100 == 0 or i == total - 1:
            elapsed = time.time() - t_start
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            remaining = (total - i - 1) / rate if rate > 0 else 0
            pct = (i + 1) / total * 100
            print(
                f"[{pct:.1f}%] {i+1}/{total} | "
                f"analyzed={analyzed} skipped={skipped} err={errors} | "
                f"{rate:.1f} f/s | ETA: {remaining/60:.1f}min",
                flush=True,
            )

    elapsed = time.time() - t_start
    print(f"\nDone! {total} files in {elapsed:.1f}s", flush=True)
    print(f"  Analyzed: {analyzed}", flush=True)
    print(f"  Skipped (cached): {skipped}", flush=True)
    print(f"  Errors: {errors}", flush=True)

    # DB stats
    count = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
    analyzed_count = conn.execute("SELECT COUNT(*) FROM analysis_cache").fetchone()[0]
    print(f"DB: {count} samples, {analyzed_count} analyzed", flush=True)

    conn.close()


if __name__ == "__main__":
    run()
