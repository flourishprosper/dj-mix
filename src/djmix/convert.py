"""Convert audio dj-mix can't analyze (m4a/AAC, Opus, OGG, WMA, WebM) to MP3.

Each file becomes a 320 kbps MP3 next to it with the same name and its
metadata copied over. The original is moved into _converted-originals/ (never
deleted), which also keeps it out of the track list.
"""
import os, shutil, subprocess
from concurrent.futures import ThreadPoolExecutor

from .util import CONVERTIBLE_EXT

ORIGINALS = "_converted-originals"


def convertible(folder):
    """Files in the folder that need converting before they can be mixed."""
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return sorted(f for f in names
                  if f.lower().endswith(CONVERTIBLE_EXT) and not f.startswith(("MIX_", "_", ".")))


def summary(files):
    """e.g. '20 files (.m4a)' for warnings."""
    exts = sorted({os.path.splitext(f)[1].lower() for f in files})
    return f"{len(files)} file{'s' if len(files) != 1 else ''} ({', '.join(exts)})"


def convert_file(folder, name, bitrate="320k", move_original=True):
    src = os.path.join(folder, name)
    out = os.path.join(folder, os.path.splitext(name)[0] + ".mp3")
    if os.path.exists(out):
        return name, "skipped: an .mp3 with that name already exists"
    tmp = out + ".part"
    res = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", src, "-map", "0:a:0", "-map_metadata", "0",
                          "-c:a", "libmp3lame", "-b:a", bitrate, "-id3v2_version", "3", "-f", "mp3", tmp],
                         capture_output=True, text=True)
    if res.returncode:
        if os.path.exists(tmp):
            os.remove(tmp)
        return name, f"failed: {res.stderr.strip()[-200:]}"
    os.replace(tmp, out)
    if move_original:
        dest = os.path.join(folder, ORIGINALS)
        os.makedirs(dest, exist_ok=True)
        shutil.move(src, os.path.join(dest, name))
    return name, "ok"


def convert_folder(folder, bitrate="320k", move_originals=True, log=print, progress=None):
    """Convert every convertible file in the folder. Returns {name: result}."""
    files = convertible(folder)
    results = {}
    if not files:
        log("Nothing to convert")
        return results
    log(f"Converting {summary(files)} to {bitrate} MP3…")
    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 4)) as ex:
        jobs = [ex.submit(convert_file, folder, f, bitrate, move_originals) for f in files]
        for n, job in enumerate(jobs, 1):
            name, result = job.result()
            results[name] = result
            log(f"  [{n}/{len(files)}] {name}: {result}")
            if progress:
                progress(n / len(files))
    ok = sum(r == "ok" for r in results.values())
    log(f"Converted {ok}/{len(files)}"
        + (f"; originals moved to {ORIGINALS}/" if move_originals and ok else ""))
    return results
