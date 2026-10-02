"""dj-mix command line. Run with no arguments to open the interface."""
import argparse, os, sys
from dataclasses import replace

from . import config
from .brand import POSITIONS, Brand
from .presets import DEFAULT_PRESET, PRESETS, Settings
from .util import fmt_time, missing_tools


def add_mix_options(p):
    g = p.add_argument_group("mixing")
    g.add_argument("--preset", choices=PRESETS, default=DEFAULT_PRESET,
                   help="genre preset (default %(default)s): " +
                        "; ".join(f"{k} = {v.description}" for k, v in PRESETS.items()))
    g.add_argument("--bars", type=int, help="transition length in bars (default: preset)")
    g.add_argument("--bpm", type=float, help="tempo the detector leans toward (default: preset)")
    g.add_argument("--seed", type=int, help="repeat a previous order")
    g.add_argument("--lufs", type=float, default=-14.0, help="target loudness (default -14, YouTube)")
    e = g.add_mutually_exclusive_group()
    e.add_argument("--keep-endings", action="store_true", help="don't cut choppy breakdowns")
    e.add_argument("--cut-endings", action="store_true", help="cut choppy breakdowns (lofi default)")


def add_brand_options(p):
    g = p.add_argument_group("brand / watermark (defaults saved in ~/.config/dj-mix/config.json)")
    g.add_argument("--credit", action="append", metavar="LINE",
                   help="credit line under each title card (repeatable)")
    g.add_argument("--no-credits", action="store_true", help="title cards without credit lines")
    g.add_argument("--accent", help="title-card accent color, e.g. '#F2A65A'")
    g.add_argument("--text-color", help="text color")
    g.add_argument("--font", help="font file (.ttf/.otf) for titles and text watermark")
    g.add_argument("--watermark", metavar="IMAGE", help="logo image to watermark (PNG with transparency)")
    g.add_argument("--watermark-text", metavar="TEXT", help="text watermark")
    g.add_argument("--no-watermark", action="store_true", help="ignore the saved watermark")
    g.add_argument("--watermark-pos", choices=POSITIONS)
    g.add_argument("--watermark-size", type=float, help="width as fraction of frame (default 0.12)")
    g.add_argument("--watermark-opacity", type=float, help="0-1 (default 0.6)")
    g.add_argument("--watermark-margin", type=float, help="fraction of frame width (default 0.03)")
    g.add_argument("--save-brand", action="store_true", help="save these brand options as defaults")


def settings_from(args):
    cut = True if args.cut_endings else False if args.keep_endings else None
    return Settings(preset=PRESETS[args.preset], bars=args.bars, bpm_center=args.bpm,
                    cut_breakdowns=cut, lufs=args.lufs, seed=args.seed)


def brand_from(args):
    b = config.brand_defaults()
    upd = {}
    if args.credit:
        upd["credits"] = args.credit
    if args.no_credits:
        upd["credits"] = []
    if args.no_watermark:
        upd.update(watermark_image=None, watermark_text=None)
    if args.watermark:
        upd.update(watermark_image=os.path.abspath(args.watermark), watermark_text=None)
    if args.watermark_text:
        upd.update(watermark_text=args.watermark_text, watermark_image=None)
    for name in ("accent", "text_color", "font", "watermark_pos", "watermark_size",
                 "watermark_opacity", "watermark_margin"):
        v = getattr(args, name)
        if v is not None:
            upd[name] = os.path.abspath(v) if name == "font" else v
    b = replace(b, **upd)
    if args.save_brand:
        config.save_brand(b)
        print(f"Saved brand defaults to {config.PATH}")
    return b


def main(argv=None):
    try:
        _main(argv)
    except RuntimeError as e:          # expected problems (no audio, nothing rendered…): no traceback
        sys.exit(f"dj-mix: {e}")


def _main(argv=None):
    ap = argparse.ArgumentParser(prog="dj-mix", description="Beat-matched DJ mixes from a folder of tracks.")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("ui", help="open the interface (default)")
    p.add_argument("folder", nargs="?")

    p = sub.add_parser("analyze", help="tempo / key / stability report per track")
    p.add_argument("folder")
    add_mix_options(p)

    p = sub.add_parser("plan", help="show the order and every transition, without rendering")
    p.add_argument("folder")
    add_mix_options(p)

    p = sub.add_parser("mix", help="render the mix (and video)")
    p.add_argument("folder")
    add_mix_options(p)
    p.add_argument("--video", help="loop clip (default: first video in the folder)")
    p.add_argument("--audio-only", action="store_true", help="just the WAV, no video")
    p.add_argument("--titles", action="store_true", help="title card at each song")
    add_brand_options(p)

    p = sub.add_parser("promo", help="cut short promo clips of songs from a rendered mix")
    p.add_argument("folder")
    p.add_argument("--render", help="which render (file name or history id; default: newest)")
    p.add_argument("--songs", help="song numbers in the mix order, e.g. 1,4,7 (default: all)")
    p.add_argument("--length", type=float, default=30, help="seconds (default 30)")
    p.add_argument("--format", choices=["vertical", "square", "original"], default="vertical")
    p.add_argument("--no-card", action="store_true", help="no title card on the clips")
    add_brand_options(p)

    p = sub.add_parser("convert", help="convert m4a/AAC/Opus/OGG/WMA audio in a folder to MP3")
    p.add_argument("folder")
    p.add_argument("--bitrate", default="320k", help="MP3 bitrate (default 320k)")
    p.add_argument("--keep-originals-in-place", action="store_true",
                   help="leave the originals where they are (default: move to _converted-originals/)")

    p = sub.add_parser("history", help="list renders and promos made in a folder")
    p.add_argument("folder")

    p = sub.add_parser("titles", help="re-encode an existing mix video with title cards / watermark")
    p.add_argument("mix", help="MIX_*.mp4 (its audio is kept as-is)")
    p.add_argument("--tracklist", help="default: <mix>_tracklist.txt")
    p.add_argument("--video", help="loop clip (default: first video next to the mix)")
    p.add_argument("--out", help="default: <mix>_titled.mp4")
    add_brand_options(p)

    args = ap.parse_args(argv)
    for name in ("folder", "mix", "video", "tracklist", "out"):
        if getattr(args, name, None):
            setattr(args, name, os.path.expanduser(getattr(args, name)))
    if args.cmd in (None, "ui"):
        from .tui import run
        run(getattr(args, "folder", None))
        return

    if missing_tools():
        sys.exit(f"Missing: {', '.join(missing_tools())}  (brew install ffmpeg rubberband)")

    from . import pipeline
    if args.cmd == "analyze":
        tracks = pipeline.analyze(args.folder, settings_from(args))
        print_analysis(tracks, settings_from(args))
    elif args.cmd == "plan":
        s = settings_from(args)
        seed, order, steps, length = pipeline.plan(args.folder, s)
        print(f"Preset {s.preset.name}, {s.bars}-bar transitions, seed {seed}\n")
        for i, t in enumerate(order, 1):
            print(f"  {i:2}. {t['file']:34} {t['bpm']:6.1f} BPM  {t['camelot']:>4}  [{t['song']}]")
        from .plan import describe
        print()
        for st in steps:
            print(f"  {fmt_time(st['at']):>6}  {st['i']:2}. -> {st['b']['file']}: {describe(st['tr'], s)}")
        print(f"\nEstimated length {fmt_time(length)}.  Render this exact plan with --seed {seed}")
    elif args.cmd == "mix":
        pipeline.mix(args.folder, settings_from(args), brand=brand_from(args), titles=args.titles,
                     video=args.video, audio_only=args.audio_only)
    elif args.cmd == "titles":
        titles_cmd(args)
    elif args.cmd == "promo":
        promo_cmd(args)
    elif args.cmd == "history":
        history_cmd(args)
    elif args.cmd == "convert":
        from .convert import convert_folder
        res = convert_folder(args.folder, args.bitrate, not args.keep_originals_in_place)
        if any(r != "ok" and not r.startswith("skipped") for r in res.values()):
            sys.exit(1)


def promo_cmd(args):
    from . import history, pipeline, promo
    pipeline.adopt_legacy(args.folder)
    cuttable = [r for r in history.renders(args.folder) if r["exists"] and r.get("video") and r.get("timeline")]
    if args.render:
        cuttable = [r for r in cuttable if args.render in (r["id"], r["video"])]
    if not cuttable:
        sys.exit("No rendered mix video to cut from in this folder (render one first)")
    r = cuttable[0]
    songs = ([int(n) - 1 for n in args.songs.split(",")] if args.songs else list(range(len(r["timeline"]))))
    print(f"Cutting {len(songs)} {args.format} clip(s) of {args.length:g}s from {r['video']}")
    outs = promo.export(args.folder, r, songs, args.length, args.format, not args.no_card, brand_from(args))
    print(f"Done: {len(outs)} clip(s) in {os.path.dirname(outs[0]) if outs else ''}")


def history_cmd(args):
    from . import history, pipeline
    pipeline.adopt_legacy(args.folder)
    for r in history.renders(args.folder):
        out = r.get("video") or r.get("audio")
        print(f"{r.get('created', ''):16}  {out:36}  {fmt_time(r.get('duration', 0)):>6}  "
              f"seed {r.get('seed', '?'):<7} {r.get('preset', '')} {r.get('bars') or ''}  "
              f"{len(r.get('promos', []))} promos{'' if r['exists'] else '  (file missing)'}")
        for p in r.get("promos", []):
            print(f"{'':18}└ {p['file']}")
    last = history.load(args.folder)["last"]
    if last:
        print(f"\nLast settings here: {last}")


def titles_cmd(args):
    from . import video as videomod
    from .brand import parse_tracklist
    from .util import list_videos, probe
    base = os.path.splitext(args.mix)[0]
    cards = parse_tracklist(args.tracklist or base + "_tracklist.txt")
    folder = os.path.dirname(os.path.abspath(args.mix))
    vid = args.video or os.path.join(folder, (list_videos(folder) or sys.exit("No loop video found"))[0])
    out = args.out or base + "_titled.mp4"
    dur = float(probe(args.mix, "format=duration"))
    print(f"Adding {len(cards)} title cards -> {out}")
    videomod.encode(vid, args.mix, out, dur, cards=cards, brand=brand_from(args), copy_audio=True)
    print(f"Done: {out}")


def print_analysis(tracks, settings):
    from .analyze import active_end
    print(f"\n{'Track':32} {'Song':20} {'BPM':>6} {'Drift':>6} {'Key':>4} {'Cam':>4} {'LUFS':>6} "
          f"{'Len':>6}  Breakdown")
    for t in sorted(tracks, key=lambda t: t["bpm"]):
        bd = (f"choppy from {fmt_time(t['clean_end'])} (cuts {fmt_time(t['duration'] - t['clean_end'])})"
              if t["clean_end"] < t["duration"] - 1 else "-")
        if bd != "-" and not settings.cut_breakdowns:
            bd += " [kept]"
        print(f"{t['file'][:32]:32} {t['song'][:20]:20} {t['bpm']:6.1f} {t['wander']:6.2f} "
              f"{t['key']:>4} {t['camelot']:>4} {t['lufs']:6.1f} {fmt_time(t['duration']):>6}  {bd}")
    print("\nDrift = std-dev of local tempo in BPM (lower = steadier; under ~1 beatmatches well).")
    print(f"Tracks within {settings.max_stretch:.0%} of each other in tempo can be beatmatched.")


if __name__ == "__main__":
    main()
