"""Audio rendering: stretch, gain, EQ transitions, write one continuous WAV."""
import os, shutil, tempfile
from collections import deque

import numpy as np

from .plan import describe, walk
from .util import SR, run


def band_split(x, cutoff=150):
    from scipy.signal import butter, sosfiltfilt
    sos = butter(4, cutoff, "low", fs=SR, output="sos")
    low = sosfiltfilt(sos, x, axis=0).astype(np.float32)
    return low, x - low


def mix_segment(a, b, beatmatched, beat_len):
    """Highs crossfade equal-power over the whole overlap; lows do a bass swap
    over one beat at the middle downbeat so two basslines never stack."""
    n = len(a)
    x = np.linspace(0, 1, n, dtype=np.float32)[:, None]
    fade_out, fade_in = np.cos(x * np.pi / 2), np.sin(x * np.pi / 2)
    if not beatmatched:
        return a * fade_out + b * fade_in
    a_lo, a_hi = band_split(a)
    b_lo, b_hi = band_split(b)
    ramp = np.clip((np.arange(n) - (n // 2 - beat_len // 2)) / beat_len, 0, 1)
    ramp = ramp.astype(np.float32)[:, None]
    return a_hi * fade_out + b_hi * fade_in + a_lo * (1 - ramp) + b_lo * ramp


def load_audio(folder, t, ratio, target_lufs, work):
    """Decode -> time-stretch (pitch preserved) -> loudness-match."""
    import soundfile as sf
    wav = os.path.join(work, "dec.wav")
    run(["ffmpeg", "-y", "-v", "error", "-i", os.path.join(folder, t["file"]),
         "-ac", "2", "-ar", str(SR), "-c:a", "pcm_f32le", wav])
    if abs(ratio - 1) > 0.0005:
        out = os.path.join(work, "str.wav")
        run(["rubberband", "-q", "-3", "-t", f"{ratio:.6f}", wav, out])
        wav = out
    x, _ = sf.read(wav, dtype="float32", always_2d=True)
    return x * np.float32(10 ** ((target_lufs - t["lufs"]) / 20))


def render(folder, order, settings, out_wav, log=print, on_track=None):
    """Render the mix. Returns (duration s, chapters [(time, track)], notes)."""
    import soundfile as sf

    chapters, notes = [(0.0, order[0])], []
    work = tempfile.mkdtemp()
    loaded = deque()                      # walk() loads track 0, then each next track

    def load(t, ratio):
        loaded.append(load_audio(folder, t, ratio, settings.lufs, work))
        return len(loaded[-1])

    try:
        with sf.SoundFile(out_wav, "w", samplerate=SR, channels=2, subtype="FLOAT", format="RF64") as out:
            written = 0
            ra, pos, cur = 1.0, 0, None
            log(f"  [1/{len(order)}] {order[0]['file']}")
            for st in walk(order, settings, load):
                if cur is None:
                    cur = loaded.popleft()
                nxt = loaded.popleft()
                tr, s, e, L = st["tr"], st["s"], st["e"], st["L"]
                beat_len = int(L / (tr["bars"] * 4)) if tr["bars"] else SR // 2
                out.write(cur[st["pos"]:s]); written += s - st["pos"]
                out.write(mix_segment(cur[s:s + L], nxt[e:e + L], tr["kind"] == "beatmatch", beat_len))
                chapters.append(((written + L // 2) / SR, st["b"]))
                written += L
                note = f"{st['i']:2}. {st['a']['file']} -> {st['b']['file']}: {describe(tr, settings)}"
                notes.append(note)
                log(f"  [{st['i'] + 1}/{len(order)}] {note}")
                if on_track:
                    on_track(st["i"] + 1, len(order))
                cur, ra, pos = nxt, st["rb"], e + L
            if cur is None:                      # single-track "mix"
                cur = loaded.popleft()
            # last track: stop before any breakdown, with a gentle fade
            last = order[-1]
            end = len(cur)
            if settings.cut_breakdowns and last["clean_end"] < last["duration"] - 1:
                end = max(pos, min(end, int(last["clean_end"] * ra * SR)))
                fade = min(6 * SR, end - pos)
                tail = cur[end - fade:end] * np.cos(np.linspace(0, np.pi / 2, fade, dtype=np.float32))[:, None]
                out.write(cur[pos:end - fade]); out.write(tail)
            else:
                out.write(cur[pos:end])
            written += end - pos
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return written / SR, chapters, notes
