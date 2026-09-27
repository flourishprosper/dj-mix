"""Promo clips: short videos of individual songs, cut from a finished mix.

Clips come from the rendered mix video (not the source files), so they carry
the mix's sound, visuals, watermark and loudness. For each song the clip is
taken from where that song plays on its own (no neighbour bleeding in),
starts on a downbeat, avoids the mix's own title card, and prefers the most
energetic stretch.
"""
import math, os, shutil, subprocess, tempfile

import numpy as np

from . import brand as brandmod
from . import history
from .plan import downbeat_phase
from .util import probe

FORMATS = {
    "original": None,              # same frame as the mix
    "vertical": (1080, 1920),      # 9:16 Reels / TikTok / Shorts
    "square": (1080, 1080),        # 1:1 feed posts
}
CARD_WINDOW = brandmod.FADE_IN + brandmod.HOLD + brandmod.FADE_OUT


# ---------------------------------------------------------------- timeline
def build_timeline(order, segs):
    """Per song: where it sits in the mix (seconds) and its downbeats in mix time."""
    out = []
    for t, seg in zip(order, segs):
        beats = np.asarray(t["beats"])
        mix = beats * seg["ratio"] + seg["shift"]
        idx = np.flatnonzero((mix >= seg["start"]) & (mix <= seg["end"]))
        if len(idx):
            solo = np.flatnonzero((mix >= seg["full_in"]) & (mix <= seg["out_start"]))
            lo, hi = (solo[0], solo[-1]) if len(solo) > 8 else (idx[0], idx[-1])
            ph = downbeat_phase(t["kick"], lo, hi + 1)
            downbeats = [round(float(mix[i]), 3) for i in idx if i % 4 == ph]
        else:
            downbeats = []
        out.append(dict(title=os.path.splitext(t["file"])[0], file=t["file"], song=t["song"],
                        start=seg["start"], full_in=seg["full_in"], out_start=seg["out_start"],
                        end=seg["end"], chapter=seg["chapter"], downbeats=downbeats))
    return out


# ---------------------------------------------------------------- choosing the clip
def _energy(video, lo, hi, rate=4000, hop=0.25):
    """RMS energy per `hop` seconds of the mix audio between lo and hi."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{lo:.3f}", "-t", f"{hi - lo:.3f}", "-i", video,
                          "-map", "0:a:0", "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"],
                         check=True, capture_output=True).stdout
    y = np.frombuffer(raw, dtype=np.float32)
    n = int(rate * hop)
    frames = y[:len(y) // n * n].reshape(-1, n) if len(y) >= n else y.reshape(1, -1)
    return np.sqrt((frames ** 2).mean(axis=1) + 1e-12), hop


def choose_clip(video, song, length, titles_in_mix=True):
    """Returns (start, length, clean) in mix seconds. clean=False means the
    song's solo part was shorter than `length`, so the clip includes some of a
    transition (or the clip was shortened)."""
    lo, hi = song["full_in"], song["out_start"]
    card_end = max(song["chapter"], brandmod.FIRST_CARD_DELAY) + CARD_WINDOW if titles_in_mix else 0
    for floor in (max(lo, card_end), lo):
        cands = [d for d in song["downbeats"] if d >= floor and d + length <= hi]
        if cands:
            break
    if not cands:
        # solo section too short: centre the clip on the song, spilling into transitions
        length = min(length, song["end"] - song["start"])
        mid = (lo + hi) / 2
        start = min(max(song["start"], mid - length / 2), song["end"] - length)
        return round(start, 3), round(length, 3), False
    e, hop = _energy(video, cands[0], cands[-1] + length)
    win = max(1, int(length / hop))
    csum = np.concatenate([[0], np.cumsum(e)])
    def score(c):
        i = int((c - cands[0]) / hop)
        j = min(len(e), i + win)
        return (csum[j] - csum[i]) / max(1, j - i)
    best = max(cands, key=score)
    return round(best, 3), float(length), True


# ---------------------------------------------------------------- export
def export_clip(video, start, length, out, fmt="original", title=None, brand=None, card=True):
    src_w, src_h = map(int, probe(video, "stream=width,height", "v:0").split(",")[:2])
    size = FORMATS[fmt]
    W, H = size or (src_w, src_h)
    work = tempfile.mkdtemp()
    try:
        inputs = ["-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", video]
        if size:
            chain = [f"[0:v]split=2[a][b]",
                     f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                     f"gblur=sigma=40,eq=brightness=-0.12[bg]",
                     f"[b]scale={W}:-2[fg]",
                     f"[bg][fg]overlay=(W-w)/2:(H-h)/2[base]"]
        else:
            chain = ["[0:v]null[base]"]
        last = "[base]"
        if card and title:
            png = os.path.join(work, "card.png")
            if size:   # place the card just under the video band, sized for phones
                band_bottom = (H + W * src_h / src_w) / 2
                brandmod.make_card(title, brand or brandmod.Brand(), W, H, png,
                                   scale=2.0 if fmt == "vertical" else 1.3, top=round(band_bottom + W * 0.05))
            else:
                brandmod.make_card(title, brand or brandmod.Brand(), W, H, png)
            inputs += ["-loop", "1", "-framerate", "24", "-t", f"{length:.3f}", "-i", png]
            chain.append(f"[1:v]format=rgba,fade=t=in:st=0.3:d=0.7:alpha=1,"
                         f"fade=t=out:st={max(0.5, length - 1.2):.3f}:d=0.8:alpha=1[card]")
            chain.append(f"{last}[card]overlay[carded]")
            last = "[carded]"
        chain.append(f"{last}fade=t=in:d=0.4,fade=t=out:st={max(0, length - 0.5):.3f}:d=0.5,"
                     f"format=yuv420p[vout]")
        cmd = ["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(chain),
               "-map", "[vout]", "-map", "0:a:0",
               "-af", f"afade=t=in:d=0.3,afade=t=out:st={max(0, length - 1):.3f}:d=1",
               "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-c:a", "aac", "-b:a", "256k",
               "-movflags", "+faststart", "-t", f"{length:.3f}", out]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode:
            raise RuntimeError(f"ffmpeg failed: {res.stderr[-800:]}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def export(folder, render, song_indices, length=30, fmt="original", card=True, brand=None,
           log=print, progress=None):
    """Cut clips for the chosen songs of a render (a history record). Returns output paths."""
    video = os.path.join(folder, render["video"])
    out_dir = os.path.join(folder, os.path.splitext(render["video"])[0] + "_promo")
    os.makedirs(out_dir, exist_ok=True)
    outs = []
    for n, i in enumerate(song_indices):
        song = render["timeline"][i]
        start, L, clean = choose_clip(video, song, length, render.get("titles", True))
        # the mix's own title card is on screen if we couldn't avoid it -> don't stack a second one
        card_end = max(song["chapter"], brandmod.FIRST_CARD_DELAY) + CARD_WINDOW
        own_card = card and not (render.get("titles", True) and start < card_end
                                 and fmt == "original")
        name = f"{i + 1:02d} {song['title']} ({fmt}, {int(round(L))}s).mp4"
        out = os.path.join(out_dir, name)
        log(f"  [{n + 1}/{len(song_indices)}] {song['title']}: {fmt_s(start)}–{fmt_s(start + L)} of the mix"
            + ("" if clean else "  (short solo section: includes part of a transition)"))
        export_clip(video, start, L, out, fmt, song["title"], brand, own_card)
        history.add_promo(folder, render["id"], dict(song=i, title=song["title"], file=os.path.relpath(out, folder),
                                                     start=start, length=L, format=fmt))
        history.log(folder, f"promo  {os.path.relpath(out, folder)}  ({fmt_s(start)}–{fmt_s(start + L)})")
        outs.append(out)
        if progress:
            progress((n + 1) / len(song_indices))
    return outs


def fmt_s(s):
    s = int(round(s))
    return f"{s // 60}:{s % 60:02d}"
