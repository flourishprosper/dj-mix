"""Track ordering and transition planning."""
import math, random

import numpy as np

from .analyze import active_end
from .util import SR, camelot_cost, local_grid


# ---------------------------------------------------------------- ordering
def plan_order(tracks, settings, seed):
    """Order tracks: never two takes of the same song back to back, then
    prefer close tempos and compatible keys. Randomised, so reruns differ;
    the same seed always gives the same order."""
    rng = random.Random(seed)
    n = len(tracks)
    max_stretch = settings.max_stretch

    def cost(a, b):
        gap = abs(math.log(a["bpm"] / b["bpm"]))
        c = camelot_cost(a["camelot"], b["camelot"]) * 0.7
        c += gap * 40 + (6 if gap > max_stretch else 0)   # can't beatmatch -> expensive
        if a["song"] == b["song"]:
            c += 100
        return c

    def feasible(j, left):
        """After placing j, can the rest still avoid same-song neighbours?
        The most common remaining song needs enough other tracks between."""
        rest = [tracks[i]["song"] for i in left if i != j]
        if not rest:
            return True
        counts = {}
        for s in rest:
            counts[s] = counts.get(s, 0) + 1
        slots = (len(rest) + 1) // 2
        return all(c <= (slots if s != tracks[j]["song"] else len(rest) // 2)
                   for s, c in counts.items())

    best, best_cost = None, float("inf")
    for _ in range(3000):
        left = list(range(n))
        starts = [i for i in left if feasible(i, left)] or left
        seq = [rng.choice(starts)]
        left.remove(seq[0])
        total = 0.0
        while left:
            prev = tracks[seq[-1]]
            prev2 = tracks[seq[-2]] if len(seq) > 1 else None
            cands = [j for j in left if feasible(j, left)] or left
            w = []
            for j in cands:
                c = cost(prev, tracks[j])
                if prev2 is not None and prev2["song"] == tracks[j]["song"]:
                    c += 1.5  # also keep same-song takes 2 apart when possible
                w.append(math.exp(-c))
            j = rng.choices(cands, weights=w)[0]
            total += cost(prev, tracks[j])
            seq.append(j)
            left.remove(j)
        total += rng.random() * 2  # keep reruns varied among near-equal orders
        if total < best_cost:
            best, best_cost = seq, total
    return [tracks[i] for i in best]


# ---------------------------------------------------------------- transitions
def downbeat_phase(kick, lo, hi):
    """Which beat index (mod 4) carries the most kick energy in beats[lo:hi]."""
    k = np.asarray(kick)
    idx = np.arange(max(0, lo), min(len(k), hi))
    return int(np.argmax([k[idx[(idx % 4) == ph]].mean() if ((idx % 4) == ph).any() else 0
                          for ph in range(4)]))


def pick_out(t, nb, not_before, end):
    """Outgoing: latest downbeat (phrase-aligned if possible) whose nb-beat
    transition finishes before the track's usable end. Returns beat index or None."""
    b = np.asarray(t["beats"])
    last = np.searchsorted(b, end) - 1
    ph = downbeat_phase(t["kick"], last - 64, last)
    cands = [i for i in range(len(b) - nb)
             if i % 4 == ph and b[i] >= not_before and b[i + nb] <= end + 0.2]
    if not cands:
        return None
    first_db = next((i for i in range(len(b)) if i % 4 == ph and b[i] >= t["active_start"]), 0)
    phrased = [i for i in cands if (i - first_db) % 16 == 0]   # 4-bar phrase boundary
    return (phrased or cands)[-1]


def pick_in(t, nb):
    """Incoming: first downbeat once the music has started."""
    b = np.asarray(t["beats"])
    start = int(np.searchsorted(b, t["active_start"] - 0.1))
    ph = downbeat_phase(t["kick"], start, start + 64)
    for i in range(start, len(b) - nb):
        if i % 4 == ph:
            return i
    return None


def plan_transition(a, ra, b, settings, not_before):
    """Decide how a (already stretched by ra) hands over to b.
    Returns times in each track's native seconds plus b's stretch ratio."""
    a_end = active_end(a, settings.cut_breakdowns)
    for nbars in (settings.bars, settings.bars // 2):
        if nbars < 2:
            break
        nb = nbars * 4
        ia, ib = pick_out(a, nb, not_before, a_end), pick_in(b, nb)
        if ia is None or ib is None:
            continue
        # fit a straight local grid to each side's beats (detected beats
        # jitter ~10-20 ms; the fitted grid is steadier than any single beat)
        a0, pa, ea = local_grid(a["beats"][ia:ia + nb + 1])
        b0, pb, eb = local_grid(b["beats"][ib:ib + nb + 1])
        rb = pa * ra / pb                                   # stretch b to a's beat period
        if abs(math.log(rb)) > settings.max_stretch:
            break
        drift = max(ea * ra, eb * rb)                       # how far real beats stray from the grid
        if drift <= settings.max_drift:
            return dict(kind="beatmatch", bars=nbars, rb=rb,
                        a_out=a0, a_end=a0 + nb * pa, b_in=b0, drift=drift)
    # plain crossfade, no stretch
    L = 8.0
    a_out = max(not_before / ra, a_end - L / ra)
    return dict(kind="crossfade", bars=0, rb=1.0,
                a_out=a_out, a_end=a_out + L / ra, b_in=b["active_start"], drift=None)


def walk(order, settings, load):
    """Step through the mix, yielding each transition with sample positions.

    `load(track, ratio)` returns the track's length in samples once stretched
    by `ratio` (the renderer loads the audio there; a dry run just estimates).
    """
    ra = 1.0
    len_cur = load(order[0], ra)
    pos = 0                               # next unwritten sample of the current track
    for i in range(1, len(order)):
        a, b = order[i - 1], order[i]
        tr = plan_transition(a, ra, b, settings, not_before=pos / SR / ra + 20)
        rb = tr["rb"]
        len_nxt = load(b, rb)
        s = int(round(tr["a_out"] * ra * SR))
        e = int(round(tr["b_in"] * rb * SR))
        L = int(round((tr["a_end"] - tr["a_out"]) * ra * SR))
        s = max(s, pos)
        L = min(L, len_cur - s, len_nxt - e)
        yield dict(i=i, a=a, b=b, tr=tr, pos=pos, s=s, e=e, L=L, ra=ra, rb=rb)
        ra, len_cur, pos = rb, len_nxt, e + L


def describe(tr, settings):
    if tr["kind"] == "beatmatch":
        return (f"beatmatched {tr['bars']} bars, stretch {100 * (tr['rb'] - 1):+.1f}%, "
                f"worst beat offset {tr['drift'] * 1000:.0f} ms")
    return "plain 8s crossfade (tempo gap or unstable beats)"


class Timeline:
    """Where every track sits in the finished mix, in mix samples. Shared by
    the renderer and the dry run so they can never disagree.

    Per track: start (starts fading in), full_in (playing alone), out_start
    (starts fading out), end (gone), chapter (middle of its incoming
    transition), and shift/ratio to map the track's own time to mix time:
    mix_sample = native_seconds * ratio * SR + shift."""

    def __init__(self, order):
        self.written = 0
        self.segs = [dict(file=order[0]["file"], start=0, full_in=0, chapter=0, shift=0, ratio=1.0)]

    def transition(self, st):
        """Call before writing a transition; returns the mix sample it starts at."""
        ws = self.written + st["s"] - st["pos"]
        L = st["L"]
        self.segs[-1].update(out_start=ws, end=ws + L)
        self.segs.append(dict(file=st["b"]["file"], start=ws, full_in=ws + L, chapter=ws + L // 2,
                              shift=ws - st["e"], ratio=st["rb"]))
        self.written = ws + L
        return ws

    def finish(self, pos, end):
        self.written += end - pos
        self.segs[-1].update(out_start=self.written, end=self.written)

    def seconds(self):
        keys = ("start", "full_in", "out_start", "end", "chapter", "shift")
        return [{k: (v / SR if k in keys else v) for k, v in seg.items()} for seg in self.segs]


def last_track_end(order, settings, len_last, pos, ra):
    """Where the last track stops (before any breakdown, if cutting)."""
    last = order[-1]
    end = len_last
    if settings.cut_breakdowns and last["clean_end"] < last["duration"] - 1:
        end = max(pos, min(end, int(last["clean_end"] * ra * SR)))
    return end


def dry_run(order, settings):
    """Plan every transition without rendering.
    Returns (steps, estimated length s, timeline segments in seconds)."""
    steps, tl = [], Timeline(order)
    for st in walk(order, settings, lambda t, r: int(t["duration"] * r * SR)):
        ws = tl.transition(st)
        st["at"] = (ws + st["L"] // 2) / SR
        steps.append(st)
    ra = steps[-1]["rb"] if steps else 1.0
    pos = steps[-1]["e"] + steps[-1]["L"] if steps else 0
    end = last_track_end(order, settings, int(order[-1]["duration"] * ra * SR), pos, ra)
    tl.finish(pos, end)
    return steps, tl.written / SR, tl.seconds()
