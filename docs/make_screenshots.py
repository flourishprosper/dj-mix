"""Regenerate the README screenshots by driving the real interface.

  uv run python docs/make_screenshots.py FOLDER [--name "Late Night Cruise"] [--keep]

FOLDER needs tracks and a loop video. Runs analyze -> plan -> a full render
with title cards and a text watermark, capturing each step to docs/images/,
plus a still of the finished video. Your saved settings are restored after,
and the rendered mix is deleted unless --keep.
"""
import argparse, asyncio, glob, os, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "images")
SIZE = (150, 46)


async def drive(folder, name, render=True):
    from djmix import config
    from djmix.tui import DJMix

    DJMix.folder = lambda self: folder          # show a friendly path, use the real one
    app = DJMix(folder)
    async with app.run_test(size=SIZE) as pilot:
        async def until(cond, timeout=900):
            t = time.monotonic()
            while not cond():
                await pilot.pause(0.25)
                if time.monotonic() - t > timeout:
                    raise TimeoutError
        await until(lambda: app.query_one("#tracks").row_count and not app.busy)
        shown = app.query_one("#folder")
        shown.value = f"~/Music/{name}"
        shown.cursor_position = 0
        await pilot.pause(0.3)
        app.save_screenshot(filename="tracks.svg", path=OUT)

        app.query_one("#seed").value = "287710"
        await pilot.press("p")
        await until(lambda: app.query_one("#plan_table").row_count and not app.busy)
        app.save_screenshot(filename="plan.svg", path=OUT)

        app.query_one("#wm_kind").value = "text"
        app.query_one("#wm_text").value = "FLOURISH$PROSPER"
        app.query_one("#wm_image").value = ""            # never show a real local path
        app.query_one("#options").scroll_to_widget(app.query_one("#wm_margin"), animate=False, top=False)
        await pilot.pause(0.5)
        app.save_screenshot(filename="brand.svg", path=OUT)

        if not render:
            return
        await pilot.press("r")
        await until(lambda: app.job_frac >= 0.6)
        await pilot.pause(1.2)                   # let the clock tick onto the status line
        app.save_screenshot(filename="render.svg", path=OUT)
        await until(lambda: not app.busy)
        app.save_screenshot(filename="done.svg", path=OUT)

        # promo: newest render is preselected; tick three songs, wait for clip times
        app.query_one("#tabs").active = "tab-promo"
        await pilot.pause(0.5)
        t = app.query_one("#promo_table")
        t.focus()
        await until(lambda: t.row_count and all("…" not in str(t.get_row_at(r)[3]) for r in range(t.row_count)))
        for r in (1, 4, 7):
            t.move_cursor(row=r)
            await pilot.press("enter")
        t.move_cursor(row=0)
        await pilot.pause(0.5)
        app.save_screenshot(filename="promo.svg", path=OUT)
        await pilot.press("e")
        await until(lambda: not app.busy)
        app.query_one("#tabs").active = "tab-history"
        await pilot.pause(0.5)
        app.save_screenshot(filename="history.svg", path=OUT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--name", default="Late Night Cruise")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--no-render", action="store_true", help="only the interface shots")
    args = ap.parse_args()
    folder = os.path.abspath(args.folder)
    os.makedirs(OUT, exist_ok=True)

    from djmix import config
    backup = config.load()
    cfg = dict(backup)
    cfg.pop("ui", None)                          # start from defaults, not your last session
    config.save(cfg)
    before = set(glob.glob(os.path.join(folder, "MIX_*")))
    # the run adds a render + promos to the folder's history; put it back afterwards
    hist = os.path.join(folder, "_dj-mix")
    hist_backup = tempfile.mkdtemp()
    if os.path.isdir(hist):
        shutil.copytree(hist, os.path.join(hist_backup, "h"))
    try:
        asyncio.run(drive(folder, args.name, render=not args.no_render))
    finally:
        config.save(backup)
        if os.path.isdir(os.path.join(hist_backup, "h")):
            for name in ("history.json", "log.txt"):
                src = os.path.join(hist_backup, "h", name)
                if os.path.exists(src):
                    shutil.copy2(src, os.path.join(hist, name))
        shutil.rmtree(hist_backup, ignore_errors=True)

    if args.no_render:
        return
    new = sorted(set(glob.glob(os.path.join(folder, "MIX_*"))) - before)
    video = next(f for f in new if f.endswith(".mp4"))
    tracklist = next(f for f in new if f.endswith("_tracklist.txt"))
    # a still of the finished video while the 2nd song's title card is up
    t2 = next(l.split()[0] for i, l in enumerate(open(tracklist)) if i == 1)
    secs = sum(int(p) * 60 ** i for i, p in enumerate(reversed(t2.split(":")))) + 8
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(secs), "-i", video, "-frames:v", "1",
                    "-q:v", "3", os.path.join(OUT, "video-frame.jpg")], check=True)
    if not args.keep:
        for f in new:
            shutil.rmtree(f) if os.path.isdir(f) else os.remove(f)
    print("Wrote", ", ".join(sorted(os.listdir(OUT))))


if __name__ == "__main__":
    main()
