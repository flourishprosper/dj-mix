"""Synthetic-audio tests: known tempos, downbeats and breakdowns.

Each genre preset must find the right tempo on a click track built at that
genre's tempo, so tuning one genre can't silently break another.
"""
import numpy as np
import pytest
import soundfile as sf

from djmix.analyze import ASR, analyze_track, find_breakdown
from djmix.plan import plan_order
from djmix.presets import PRESETS, Settings


def drum_loop(bpm, seconds=90, sr=ASR, swing_hats=True):
    """Kick on 1 & 3 (accented on 1), snare on 2 & 4, hats on 8ths."""
    y = np.zeros(int(seconds * sr), dtype=np.float32)
    beat = 60 / bpm
    t = np.arange(int(0.12 * sr)) / sr
    kick = np.sin(2 * np.pi * 55 * t) * np.exp(-t * 30)
    snare = np.random.default_rng(0).normal(0, 1, len(t)) * np.exp(-t * 40) * 0.5
    hat = np.random.default_rng(1).normal(0, 1, len(t) // 4) * np.exp(-np.arange(len(t) // 4) / sr * 200) * 0.2
    for k in range(int(seconds / beat) - 1):
        i = int(k * beat * sr)
        if k % 4 == 0:
            y[i:i + len(kick)] += 1.0 * kick
        elif k % 4 == 2:
            y[i:i + len(kick)] += 0.6 * kick
        else:
            y[i:i + len(snare)] += snare
        for h in (0, 0.5):
            j = int((k + h) * beat * sr)
            y[j:j + len(hat)] += hat
    return y / np.abs(y).max() * 0.8


@pytest.mark.parametrize("preset,bpm", [("lofi", 86), ("hiphop", 94), ("house", 124),
                                        ("techno", 132), ("dnb", 174)])
def test_tempo_per_genre(tmp_path, preset, bpm):
    path = tmp_path / f"loop_{bpm}.wav"
    sf.write(path, drum_loop(bpm), ASR)
    p = PRESETS[preset]
    r = analyze_track(str(path), (p.bpm_center, p.bpm_spread, p.bpm_min, p.bpm_max))
    assert r["ok"]
    assert abs(r["bpm"] - bpm) / bpm < 0.01, f"{preset}: found {r['bpm']:.2f}, expected {bpm}"
    assert r["wander"] < 1.0


def test_downbeat_is_accented_kick(tmp_path):
    from djmix.plan import downbeat_phase
    path = tmp_path / "loop.wav"
    sf.write(path, drum_loop(90), ASR)
    r = analyze_track(str(path))
    ph = downbeat_phase(r["kick"], 8, 72)
    first_db = r["beats"][next(i for i in range(8, 72) if i % 4 == ph)]
    # true downbeats are at multiples of 4 beats from t=0
    bars = first_db / (4 * 60 / 90)
    assert abs(bars - round(bars)) < 0.05


def test_breakdown_detected_and_clean_track_untouched():
    # real music has a continuous bed under the drums; add a sustained pad chord
    t = np.arange(int(150 * ASR)) / ASR
    pad = sum(np.sin(2 * np.pi * f * t) for f in (220, 277.2, 329.6)).astype(np.float32) * 0.1
    y = drum_loop(86, seconds=150) + pad
    assert find_breakdown(y, 150) == 150            # a normal track is left alone
    rng = np.random.default_rng(2)
    tail = y.copy()
    start = int(100 * ASR)                          # from 100 s: flickering packets of sound
    for i in range(start, len(tail), int(0.1 * ASR)):
        if rng.random() < 0.5:
            tail[i:i + int(0.1 * ASR)] = 0
    cut = find_breakdown(tail, 150)
    assert 95 <= cut <= 105, cut


def test_same_song_takes_never_adjacent():
    tracks = [dict(file=f"{s}{n}", song=s, bpm=86 + n, camelot="8B")
              for s, count in (("a", 8), ("b", 3), ("c", 3), ("d", 2)) for n in range(count)]
    for seed in range(20):
        order = plan_order(tracks, Settings(), seed)
        assert all(x["song"] != y["song"] for x, y in zip(order, order[1:])), seed
        assert sorted(t["file"] for t in order) == sorted(t["file"] for t in tracks)
