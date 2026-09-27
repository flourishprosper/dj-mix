"""The whole job, shared by the CLI and the interface: analyze -> plan -> render -> encode."""
import os, random, time

from . import video as videomod
from .analyze import analyze_folder
from .plan import dry_run, plan_order
from .render import render
from .util import fmt_time, list_videos


def analyze(folder, settings, log=print):
    tracks = [t for t in analyze_folder(folder, settings.tempo_prior, progress=log) if t.get("ok")]
    if not tracks:
        raise RuntimeError(f"No usable audio in {folder}")
    return tracks


def plan(folder, settings, log=print):
    """Returns (seed, order, steps, estimated length)."""
    tracks = analyze(folder, settings, log)
    seed = settings.seed if settings.seed is not None else random.randrange(10 ** 6)
    order = plan_order(tracks, settings, seed)
    steps, length = dry_run(order, settings)
    return seed, order, steps, length


def mix(folder, settings, *, brand=None, titles=False, video=None, audio_only=False,
        log=print, progress=None):
    """Full render. Returns dict of output paths and stats."""
    t0 = time.monotonic()
    seed, order, _, _ = plan(folder, settings, log)
    log(f"{settings.bars}-bar transitions, preset {settings.preset.name}, seed {seed}")
    for i, t in enumerate(order, 1):
        log(f"  {i:2}. {t['file']:34} {t['bpm']:6.1f} BPM  {t['camelot']:>4}  [{t['song']}]")

    base = os.path.join(folder, f"MIX_{time.strftime('%Y%m%d_%H%M%S')}")
    wav = base + ".wav"
    log("Rendering audio...")
    dur, chapters, notes = render(folder, order, settings, wav, log=log,
                                  on_track=(lambda i, n: progress((1 if audio_only else 0.5) * i / n)) if progress else None)

    tracklist = base + "_tracklist.txt"
    with open(tracklist, "w") as f:
        for ts, t in chapters:
            f.write(f"{fmt_time(ts)} {os.path.splitext(t['file'])[0]}\n")
        f.write(f"\n(seed {seed}, preset {settings.preset.name}, {settings.bars} bars)\n")
    beat = sum("beatmatched" in n for n in notes)
    log(f"Mix length {fmt_time(dur)}; {beat}/{len(notes)} transitions beatmatched")
    result = dict(seed=seed, duration=dur, tracklist=tracklist, notes=notes)

    if audio_only:
        result["audio"] = wav
        log(f"Audio: {os.path.basename(wav)} (in the track folder)")
        return finish(result, t0, log)

    if video is None:
        vids = list_videos(folder)
        if not vids:
            raise RuntimeError(f"No video file found in {folder} (or use audio-only)")
        video = os.path.join(folder, vids[0])
    log(f"Encoding video over {os.path.basename(video)}"
        f"{' with title cards' if titles else ''}{' + watermark' if brand and brand.has_watermark else ''}...")
    cards = [(ts, os.path.splitext(t["file"])[0]) for ts, t in chapters] if titles else []
    out = base + ".mp4"
    videomod.encode(video, wav, out, dur, cards=cards, brand=brand,
                    progress=(lambda f: progress(0.5 + 0.5 * f)) if progress else None)
    os.remove(wav)
    result["video"] = out
    log(f"Done: {os.path.basename(out)} (in the track folder)")
    return finish(result, t0, log)


def finish(result, t0, log):
    """Record how long the render took: logged, returned, and saved in the tracklist."""
    result["elapsed"] = time.monotonic() - t0
    log(f"Rendered in {fmt_time(result['elapsed'])}")
    with open(result["tracklist"], "a") as f:
        f.write(f"(rendered in {fmt_time(result['elapsed'])} on {time.strftime('%Y-%m-%d %H:%M')})\n")
    return result
