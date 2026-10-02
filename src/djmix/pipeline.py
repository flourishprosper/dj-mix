"""The whole job, shared by the CLI and the interface: analyze -> plan -> render -> encode.
Every render is recorded in the folder's history (see history.py)."""
import glob, os, random, time
from dataclasses import asdict

from . import history
from . import video as videomod
from .analyze import analyze_folder
from .plan import dry_run, plan_order
from .presets import PRESETS, Settings
from .promo import build_timeline
from .render import render
from .util import display_title, fmt_time, list_videos


def analyze(folder, settings, log=print):
    from .convert import convertible, summary
    todo = convertible(folder)
    if todo:
        log(f"⚠ {summary(todo)} can't be mixed until converted to MP3: "
            f"run `dj-mix convert \"{folder}\"` (or press c in the app)")
    every = analyze_folder(folder, settings.tempo_prior, progress=log)
    tracks = [t for t in every if t.get("ok")]
    skipped = [t for t in every if not t.get("ok")]
    if skipped:
        log(f"Skipped {len(skipped)} file(s): " + "; ".join(f"{t['file']} ({t.get('reason')})" for t in skipped))
    if not tracks:
        raise RuntimeError(f"No usable audio in {folder}"
                           + (f" — {summary(todo)} can be converted to MP3 first (press c, or dj-mix convert)"
                              if todo else ""))
    return tracks


def plan(folder, settings, log=print):
    """Returns (seed, order, steps, estimated length)."""
    tracks = analyze(folder, settings, log)
    seed = settings.seed if settings.seed is not None else random.randrange(10 ** 6)
    order = plan_order(tracks, settings, seed)
    steps, length, _ = dry_run(order, settings)
    return seed, order, steps, length


def settings_record(settings, seed):
    """The settings that reproduce a mix (saved in history, restored on reopen)."""
    p = settings.preset
    return dict(seed=seed, preset=p.name, bars=settings.bars,
                bpm=None if settings.bpm_center == p.bpm_center else settings.bpm_center,
                lufs=settings.lufs, cut=settings.cut_breakdowns)


def settings_from_record(r):
    p = PRESETS.get(r.get("preset") or "lofi", PRESETS["lofi"])
    return Settings(preset=p, bars=r.get("bars") or p.bars, bpm_center=r.get("bpm"),
                    cut_breakdowns=r.get("cut"), lufs=r.get("lufs", -14.0), seed=r.get("seed"))


def mix(folder, settings, *, brand=None, titles=False, video=None, audio_only=False,
        log=print, progress=None):
    """Full render. Returns dict of output paths and stats (and the history record)."""
    t0 = time.monotonic()
    seed, order, _, _ = plan(folder, settings, log)
    log(f"{settings.bars}-bar transitions, preset {settings.preset.name}, seed {seed}")
    for i, t in enumerate(order, 1):
        log(f"  {i:2}. {t['file']:34} {t['bpm']:6.1f} BPM  {t['camelot']:>4}  [{t['song']}]")
    history.log(folder, f"render started  seed {seed}, preset {settings.preset.name}, "
                        f"{settings.bars} bars, {len(order)} tracks")

    base = os.path.join(folder, f"MIX_{time.strftime('%Y%m%d_%H%M%S')}")
    wav = base + ".wav"
    log("Rendering audio...")
    dur, chapters, notes, segs = render(
        folder, order, settings, wav, log=log,
        on_track=(lambda i, n: progress((1 if audio_only else 0.5) * i / n)) if progress else None)

    tracklist = base + "_tracklist.txt"
    with open(tracklist, "w") as f:
        for ts, t in chapters:
            f.write(f"{fmt_time(ts)} {display_title(t['file'])}\n")
        f.write(f"\n(seed {seed}, preset {settings.preset.name}, {settings.bars} bars)\n")
    beat = sum("beatmatched" in n for n in notes)
    log(f"Mix length {fmt_time(dur)}; {beat}/{len(notes)} transitions beatmatched")
    result = dict(seed=seed, duration=dur, tracklist=tracklist, notes=notes)
    record = dict(settings_record(settings, seed), duration=dur, beatmatched=beat,
                  transitions=len(notes), titles=bool(titles), tracklist=os.path.basename(tracklist),
                  brand=asdict(brand) if brand else None, timeline=build_timeline(order, segs))

    if audio_only:
        result["audio"] = wav
        record["audio"] = os.path.basename(wav)
        log(f"Audio: {os.path.basename(wav)} (in the track folder)")
        return finish(folder, result, record, t0, log)

    if video is None:
        vids = list_videos(folder)
        if not vids:
            raise RuntimeError(f"No video file found in {folder} (or use audio-only)")
        video = os.path.join(folder, vids[0])
    log(f"Encoding video over {os.path.basename(video)}"
        f"{' with title cards' if titles else ''}{' + watermark' if brand and brand.has_watermark else ''}...")
    cards = [(ts, display_title(t["file"])) for ts, t in chapters] if titles else []
    out = base + ".mp4"
    videomod.encode(video, wav, out, dur, cards=cards, brand=brand,
                    progress=(lambda f: progress(0.5 + 0.5 * f)) if progress else None)
    os.remove(wav)
    result["video"] = out
    record.update(video=os.path.basename(out), source_video=os.path.basename(video))
    log(f"Done: {os.path.basename(out)} (in the track folder)")
    return finish(folder, result, record, t0, log)


def finish(folder, result, record, t0, log):
    """Record how long the render took (log, tracklist, history) and save the render."""
    result["elapsed"] = time.monotonic() - t0
    log(f"Rendered in {fmt_time(result['elapsed'])}")
    with open(result["tracklist"], "a") as f:
        f.write(f"(rendered in {fmt_time(result['elapsed'])} on {time.strftime('%Y-%m-%d %H:%M')})\n")
    record["elapsed"] = result["elapsed"]
    result["record"] = history.add_render(folder, record)
    history.remember_settings(folder, **settings_record(settings_from_record(record), record["seed"]),
                              titles=record["titles"], video=record.get("source_video"))
    out = record.get("video") or record.get("audio")
    history.log(folder, f"render finished  {out}  ({fmt_time(result['duration'])} mix, "
                        f"{record['beatmatched']}/{record['transitions']} beatmatched, "
                        f"rendered in {fmt_time(result['elapsed'])})")
    return result


# ---------------------------------------------------------------- older renders
def adopt_legacy(folder, log=print):
    """Add renders made before per-folder history existed. Their song timeline is
    rebuilt from the seed in the tracklist and checked against its timestamps;
    if it doesn't match, the render is listed but can't be cut into promos."""
    known = {r.get("video") or r.get("audio") for r in history.load(folder)["renders"]}
    tracklists = glob.glob(os.path.join(folder, "MIX_*_tracklist.txt"))
    adopted = 0
    for vid in sorted(glob.glob(os.path.join(folder, "MIX_*.mp4"))):
        name = os.path.basename(vid)
        if name in known:
            continue
        stem = os.path.splitext(vid)[0]
        tl = max((t for t in tracklists if stem.startswith(t[:-len("_tracklist.txt")])), key=len, default=None)
        record = dict(video=name, legacy=True, tracklist=os.path.basename(tl) if tl else None, titles=True,
                      created=time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(vid))))
        if tl:
            stamps, seed, preset, bars = history.parse_tracklist(tl)
            record.update(seed=seed, preset=preset or "lofi")
            if seed is not None:
                try:
                    settings = Settings(preset=PRESETS[preset or "lofi"], bars=bars, seed=seed)
                    record.update(settings_record(settings, seed))
                    tracks = analyze(folder, settings, log=lambda m: None)
                    order = plan_order(tracks, settings, seed)
                    steps, length, segs = dry_run(order, settings)
                    chapters = [(0.0, order[0])] + [(st["at"], st["b"]) for st in steps]
                    ok = len(chapters) == len(stamps) and all(
                        display_title(t["file"]) == title and abs(ts - s) <= 1.5
                        for (ts, t), (s, title) in zip(chapters, stamps))
                    if ok:
                        record.update(timeline=build_timeline(order, segs), duration=length,
                                      beatmatched=sum(st["tr"]["kind"] == "beatmatch" for st in steps),
                                      transitions=len(steps))
                    else:
                        record["note"] = "timeline couldn't be rebuilt (tracks or settings changed)"
                except Exception as e:  # never let an old file break opening the folder
                    record["note"] = f"timeline couldn't be rebuilt: {e}"
        history.add_render(folder, record)
        history.log(folder, f"added earlier render {name} to history"
                            + (" (promo-ready)" if record.get("timeline") else f" ({record.get('note', 'no tracklist')})"))
        adopted += 1
    if adopted:
        log(f"Added {adopted} earlier render(s) to this folder's history")
    return adopted
