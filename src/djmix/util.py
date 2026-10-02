import os, re, subprocess

import numpy as np

AUDIO_EXT = (".mp3", ".wav", ".flac", ".aif", ".aiff")          # what dj-mix mixes
CONVERTIBLE_EXT = (".m4a", ".aac", ".opus", ".ogg", ".oga", ".wma", ".webm")  # `dj-mix convert` -> mp3
VIDEO_EXT = (".mp4", ".mov")
SR = 48000            # render rate

PITCHES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def probe(path, entries, stream=None):
    cmd = ["ffprobe", "-v", "error"] + (["-select_streams", stream] if stream else []) + \
          ["-show_entries", entries, "-of", "csv=p=0", path]
    return run(cmd).stdout.strip()


def fmt_time(s):
    s = int(round(s))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def list_tracks(folder):
    return sorted(f for f in os.listdir(folder)
                  if f.lower().endswith(AUDIO_EXT) and not f.startswith(("MIX_", "_", ".")))


def list_videos(folder):
    return sorted(f for f in os.listdir(folder)
                  if f.lower().endswith(VIDEO_EXT) and not f.startswith(("MIX_", "_", ".")))


def camelot(pc, minor):
    return f"{(7 * pc + (4 if minor else 7)) % 12 + 1}{'A' if minor else 'B'}"


def camelot_cost(a, b):
    """0 = same key, 1 = adjacent / relative, grows with distance on the wheel."""
    na, la, nb, lb = int(a[:-1]), a[-1], int(b[:-1]), b[-1]
    d = min((na - nb) % 12, (nb - na) % 12)
    if la == lb:
        return d
    return 1 if d == 0 else d + 1.5


UUID_SUFFIX = re.compile(r"\s*-\s*[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def display_title(file):
    """A track's title for tracklists, title cards and the player: the file name
    without extension or a trailing download id ("Song - 7a1371ec-0e85-...")."""
    return UUID_SUFFIX.sub("", os.path.splitext(os.path.basename(file))[0]).strip()


def song_key(name):
    """Group alternate takes: drop download ids and take numbers like "(1)"."""
    name = UUID_SUFFIX.sub("", name)
    return re.sub(r"\s*\(\d+\)$", "", name).strip().lower()


def song_id(path):
    """Song identity from the embedded title tag (survives file renames),
    with take numbers like "(1)" stripped so alternate takes group together."""
    try:
        title = probe(path, "format_tags=title")
    except subprocess.CalledProcessError:
        title = ""
    return song_key(title or os.path.splitext(os.path.basename(path))[0])


def integrated_lufs(path):
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-map", "0:a",
                          "-af", "ebur128", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", out)
    return float(m[-1]) if m else -14.0


def missing_tools():
    import shutil
    return [t for t in ("ffmpeg", "ffprobe", "rubberband") if not shutil.which(t)]


def local_grid(beats):
    """Straight-line fit to a run of beats: (first beat, period, p90 residual)."""
    b = np.asarray(beats)
    k = np.arange(len(b))
    p, t0 = np.polyfit(k, b, 1)
    return float(t0), float(p), float(np.percentile(np.abs(b - (t0 + k * p)), 90))
