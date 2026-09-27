"""Per-track analysis: tempo, beats, downbeat energy, key, loudness, breakdowns.

Results are cached in <folder>/_dj-mix/analysis.json, keyed on file size/mtime
and on the tempo prior (a different preset can change the detected tempo).
"""
import json, os
from concurrent.futures import ProcessPoolExecutor
from functools import partial

import numpy as np

from .util import PITCHES, camelot, integrated_lufs, list_tracks, song_id

ASR = 22050           # analysis rate
HOP = 256
FPS = ASR / HOP
LEGACY_CACHE = "_dj-analysis.json"   # pre-history location, migrated on first use
CACHE_VERSION = 4

# Krumhansl-Schmuckler key profiles
MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def estimate_tempo(onset, center=88, spread=0.35, bpm_min=50, bpm_max=200):
    """Global tempo from onset autocorrelation, weighted toward `center` so
    double-time hats and swing/triplet pulses don't win."""
    o = onset - onset.mean()
    ac = np.fft.irfft(np.abs(np.fft.rfft(o, 2 * len(o))) ** 2)[:len(o)]
    lags = np.arange(int(FPS * 60 / bpm_max), int(FPS * 60 / bpm_min))
    a = ac[lags]
    bpm = 60 * FPS / lags
    i = int(np.argmax(a * np.exp(-0.5 * (np.log2(bpm / center) / spread) ** 2)))
    lag = float(lags[i])
    if 0 < i < len(a) - 1:  # parabolic peak refinement
        den = a[i - 1] - 2 * a[i] + a[i + 1]
        if den:
            lag += (a[i - 1] - a[i + 1]) / (2 * den)
    return 60 * FPS / lag


def track_beats(onset, bpm, tightness=100):
    """Dynamic-programming beat tracker (Ellis 2007): beats that sit on
    strong onsets while keeping spacing close to the tempo. Follows drift.
    (librosa's own beat_track crashes in numba on some macOS setups.)"""
    tau = 60 * FPS / bpm
    o = onset / (onset.std() + 1e-9)
    n = len(o)
    offs = np.arange(int(round(tau / 2)), int(round(2 * tau)) + 1)
    pen = -tightness * np.log(offs / tau) ** 2
    score = np.zeros(n)
    back = np.full(n, -1)
    for t in range(n):
        prev = t - offs
        ok = prev >= 0
        if ok.any():
            cand = score[prev[ok]] + pen[ok]
            j = int(np.argmax(cand))
            score[t] = o[t] + cand[j]
            back[t] = prev[ok][j]
        else:
            score[t] = o[t]
    tail = np.arange(max(0, n - int(2 * tau)), n)
    t = int(tail[np.argmax(score[tail])])
    beats = []
    while t >= 0:
        beats.append(t)
        t = back[t]
    return np.array(beats[::-1]) / FPS


def find_breakdown(y, dur, win_s=5.0):
    """Where the track degrades into choppy 'packets' of sound (a common Suno
    artifact, especially in extended tracks). Returns the time the clean part
    ends, or dur if the track stays clean.

    Counts sound -> gap dropouts (20 ms frames > 12 dB under the track's body
    level) per 5 s window. A normal fade drops out once; a breakdown flickers
    on and off many times. The breakdown starts at the first choppy window
    from which at least 40% of the remaining windows are also choppy."""
    import librosa
    hop = ASR // 50
    r = 20 * np.log10(librosa.feature.rms(y=y, frame_length=2 * hop, hop_length=hop)[0] + 1e-9)
    body = np.median(r[:max(1, int(len(r) * 0.4))])
    quiet = r < body - 12
    drops = np.flatnonzero(quiet[1:] & ~quiet[:-1])
    w = int(win_s * 50)
    nwin = len(r) // w
    choppy = np.array([np.sum((drops >= i * w) & (drops < (i + 1) * w)) >= 3 and
                       quiet[i * w:(i + 1) * w].mean() >= 0.1 for i in range(nwin)])
    for i in range(nwin):
        if choppy[i] and choppy[i:].mean() >= 0.4:
            t = i * win_s
            return float(t) if t >= 60 else dur   # too early = probably just a sparse track
    return dur


def analyze_track(path, tempo_prior=(88, 0.35, 50, 200)):
    import librosa

    y, _ = librosa.load(path, sr=ASR, mono=True)
    dur = len(y) / ASR
    onset = librosa.onset.onset_strength(y=y, sr=ASR, hop_length=HOP)
    bpm0 = estimate_tempo(onset, *tempo_prior)
    beats = track_beats(onset, bpm0)
    if len(beats) < 32:
        return dict(file=os.path.basename(path), ok=False, reason="too few beats")
    ibi = np.diff(beats)
    bpm = 60 / float(np.median(ibi))
    # stability: how much local tempo (16-beat windows) wanders
    loc = [60 / np.median(ibi[i:i + 16]) for i in range(0, len(ibi) - 16, 8)]
    wander = float(np.std(loc)) if loc else 0.0

    # kick energy at each beat (low-band spectral flux) -> downbeat detection later
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=HOP))
    freqs = librosa.fft_frequencies(sr=ASR, n_fft=2048)
    flux = np.concatenate([[0], np.maximum(0, np.diff(S[freqs < 150], axis=1)).sum(axis=0)])
    fr = np.clip(np.round(beats * FPS).astype(int), 0, len(flux) - 1)
    kick = [float(flux[max(0, f - 2):f + 3].max()) for f in fr]

    # active region (skip leading silence / trailing fade)
    rms = librosa.feature.rms(y=y, hop_length=HOP)[0]
    rdb = 20 * np.log10(rms + 1e-9)
    act = np.where(rdb > np.median(rdb) - 18)[0]

    chroma = librosa.feature.chroma_cqt(y=y, sr=ASR, hop_length=4096).mean(axis=1)
    _, pc, mi = max((np.corrcoef(np.roll(prof, pc), chroma)[0, 1], pc, mi)
                    for pc in range(12) for mi, prof in ((False, MAJOR), (True, MINOR)))

    return dict(
        file=os.path.basename(path), ok=True, duration=dur,
        bpm=bpm, wander=wander, beats=beats.round(4).tolist(), kick=kick,
        active_start=float(act[0] / FPS), active_end_raw=float(act[-1] / FPS),
        clean_end=find_breakdown(y, dur),
        key=f"{PITCHES[pc]}{'m' if mi else ''}", camelot=camelot(pc, mi),
        lufs=integrated_lufs(path), song=song_id(path),
    )


def active_end(t, cut_breakdowns):
    """Where the usable music ends: before the trailing fade, and (optionally)
    before any choppy breakdown."""
    return min(t["active_end_raw"], t["clean_end"]) if cut_breakdowns else t["active_end_raw"]


def analyze_folder(folder, tempo_prior=(88, 0.35, 50, 200), progress=print):
    from . import history
    cache_path = history.path(folder, "analysis.json")
    legacy = os.path.join(folder, LEGACY_CACHE)
    if not os.path.exists(cache_path) and os.path.exists(legacy):
        os.replace(legacy, cache_path)
    try:
        cache = json.load(open(cache_path))
    except (OSError, ValueError):
        cache = {}
    files = list_tracks(folder)
    prior = ",".join(f"{v:g}" for v in tempo_prior)
    sig = {f: f"{CACHE_VERSION}:{prior}:{os.path.getsize(os.path.join(folder, f))}:"
              f"{os.path.getmtime(os.path.join(folder, f))}" for f in files}
    todo = [f for f in files if cache.get(f, {}).get("_sig") != sig[f]]
    if todo:
        progress(f"Analyzing {len(todo)} track(s)...")
        job = partial(analyze_track, tempo_prior=tuple(tempo_prior))
        with ProcessPoolExecutor() as ex:
            for n, (f, r) in enumerate(zip(todo, ex.map(job, [os.path.join(folder, f) for f in todo])), 1):
                r["_sig"] = sig[f]
                cache[f] = r
                progress(f"  [{n}/{len(todo)}] {f}")
        cache = {f: cache[f] for f in files}
        with open(cache_path, "w") as fh:
            json.dump(cache, fh)
    return [cache[f] for f in files]
