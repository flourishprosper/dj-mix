"""Terminal interface: pick a folder, set options, preview the plan, render."""
import os
from dataclasses import asdict, replace
from pathlib import Path

from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (Button, DataTable, DirectoryTree, Footer, Header, Input, Label,
                             ProgressBar, RichLog, Select, Static, Switch, TabbedContent, TabPane)

from . import config
from .brand import POSITIONS, Brand
from .presets import DEFAULT_PRESET, PRESETS, Settings
from .util import fmt_time, list_videos, missing_tools

AUDIO_ONLY = "__audio_only__"


class FolderTree(DirectoryTree):
    def filter_paths(self, paths):
        return [p for p in paths if p.is_dir() and not p.name.startswith((".", "__"))]


def row(label, widget):
    return Horizontal(Label(label, classes="lbl"), widget, classes="row")


class DJMix(App):
    TITLE = "dj-mix"
    CSS = """
    #browser { width: 32; border-right: solid $panel; }
    #browser Static { padding: 0 1; color: $text-muted; }
    #options { width: 52; border-right: solid $panel; padding: 0 1; }
    .section { margin: 1 0 0 0; color: $accent; text-style: bold; }
    .row { height: auto; }
    .row .lbl { width: 20; padding: 1 1 0 0; color: $text-muted; }
    .row Input, .row Select { width: 1fr; }
    .row Switch { margin: 0 0 0 0; }
    #buttons { height: auto; margin: 1 0; }
    #buttons Button { width: 1fr; margin: 0 1 0 0; }
    #status { height: 1; padding: 0 1; color: $text-muted; }
    #progress { padding: 0 1; }
    #tabs { height: 1fr; }
    #preset_desc { padding: 0 0 0 20; color: $text-muted; }
    DataTable { height: 1fr; }
    """
    BINDINGS = [("a", "analyze", "Analyze"), ("p", "plan", "Plan"),
                ("r", "render", "Render"), ("q", "quit", "Quit")]

    def __init__(self, folder=None):
        super().__init__()
        self.cfg = config.load()
        self.ui = self.cfg.get("ui", {})
        self.brand = config.brand_defaults()
        self.start_folder = folder or self.ui.get("folder", "")
        self.busy = False

    # ------------------------------------------------------------ layout
    def compose(self) -> ComposeResult:
        ui, b = self.ui, self.brand
        root = self.ui.get("root") or (str(Path(self.start_folder).parent) if self.start_folder else os.getcwd())
        preset = ui.get("preset", DEFAULT_PRESET)
        yield Header()
        with Horizontal():
            with Vertical(id="browser"):
                yield Static("Music folders — Enter to open")
                yield FolderTree(root, id="tree")
            with VerticalScroll(id="options"):
                yield row("Folder", Input(self.start_folder, id="folder", placeholder="path to a folder of tracks"))

                yield Label("Mixing", classes="section")
                yield row("Preset", Select([(k, k) for k in PRESETS],
                                           value=preset, allow_blank=False, id="preset"))
                yield Static(PRESETS[preset].description, id="preset_desc")
                yield row("Transition (bars)", Select([(str(n), n) for n in (2, 4, 8, 16, 32)],
                                                      value=ui.get("bars", PRESETS[preset].bars),
                                                      allow_blank=False, id="bars"))
                yield row("Tempo lean (BPM)", Input(str(ui.get("bpm", "")), id="bpm", type="number",
                                                    placeholder=f"preset: {PRESETS[preset].bpm_center:g}"))
                yield row("Loudness (LUFS)", Input(str(ui.get("lufs", -14.0)), id="lufs", type="number"))
                yield row("Seed", Input("", id="seed", type="integer", placeholder="random"))
                yield row("Cut choppy endings", Switch(ui.get("cut", PRESETS[preset].cut_breakdowns), id="cut"))

                yield Label("Output", classes="section")
                yield row("Video", Select([], id="video", prompt="(no video in folder)"))
                yield row("Title cards", Switch(ui.get("titles", True), id="titles"))

                yield Label("Brand", classes="section")
                credits = b.credits + ["", ""]
                yield row("Credit line 1", Input(credits[0], id="credit1"))
                yield row("Credit line 2", Input(credits[1], id="credit2"))
                yield row("Accent color", Input(b.accent, id="accent"))
                yield row("Text color", Input(b.text_color, id="text_color"))
                yield row("Font file", Input(b.font or "", id="font", placeholder="default: Avenir Next"))
                kind = "image" if b.watermark_image else "text" if b.watermark_text else "none"
                yield row("Watermark", Select([("None", "none"), ("Text", "text"), ("Logo image", "image")],
                                              value=kind, allow_blank=False, id="wm_kind"))
                yield row("Watermark text", Input(b.watermark_text or "", id="wm_text"))
                yield row("Logo image path", Input(b.watermark_image or "", id="wm_image",
                                                   placeholder="PNG with transparency"))
                yield row("Position", Select([(p, p) for p in POSITIONS], value=b.watermark_pos,
                                             allow_blank=False, id="wm_pos"))
                yield row("Size (0-1)", Input(str(b.watermark_size), id="wm_size", type="number"))
                yield row("Opacity (0-1)", Input(str(b.watermark_opacity), id="wm_opacity", type="number"))
                yield row("Margin (0-1)", Input(str(b.watermark_margin), id="wm_margin", type="number"))

                with Horizontal(id="buttons"):
                    yield Button("Analyze [a]", id="analyze")
                    yield Button("Plan [p]", id="plan", variant="primary")
                    yield Button("Render [r]", id="render", variant="success")
            with Vertical():
                with TabbedContent(id="tabs"):
                    with TabPane("Tracks", id="tab-tracks"):
                        yield DataTable(id="tracks", zebra_stripes=True, cursor_type="row")
                    with TabPane("Plan", id="tab-plan"):
                        yield DataTable(id="plan_table", zebra_stripes=True, cursor_type="row")
                    with TabPane("Log", id="tab-log"):
                        yield RichLog(id="log", wrap=True, markup=True)
                yield ProgressBar(id="progress", total=100, show_eta=False)
                yield Static("", id="status")
        yield Footer()

    def on_mount(self):
        self.query_one("#tracks", DataTable).add_columns(
            "Track", "Song", "BPM", "Drift", "Key", "LUFS", "Length", "Breakdown")
        self.query_one("#plan_table", DataTable).add_columns(
            "#", "Track", "BPM", "Key", "In at", "Transition in")
        self.ready = False
        self.call_after_refresh(self._after_mount)
        if missing_tools():
            self.status(f"[red]Missing: {', '.join(missing_tools())} — brew install ffmpeg rubberband")

    def _after_mount(self):
        self.ready = True
        if self.start_folder and os.path.isdir(self.start_folder):
            self.load_folder(self.start_folder)

    # ------------------------------------------------------------ helpers
    def status(self, msg):
        self.query_one("#status", Static).update(msg)

    def log_line(self, msg):
        self.query_one("#log", RichLog).write(msg)

    def val(self, wid):
        return self.query_one(f"#{wid}").value

    def settings(self):
        bpm, seed = self.val("bpm").strip(), self.val("seed").strip()
        return Settings(preset=PRESETS[self.val("preset")], bars=int(self.val("bars")),
                        bpm_center=float(bpm) if bpm else None, cut_breakdowns=self.val("cut"),
                        lufs=float(self.val("lufs") or -14), seed=int(seed) if seed else None)

    def brand_now(self):
        kind = self.val("wm_kind")
        return Brand(
            credits=[c for c in (self.val("credit1").strip(), self.val("credit2").strip()) if c],
            accent=self.val("accent").strip() or "#F2A65A",
            text_color=self.val("text_color").strip() or "#F6ECDC",
            font=self.val("font").strip() or None,
            watermark_text=(self.val("wm_text").strip() or None) if kind == "text" else None,
            watermark_image=(os.path.expanduser(self.val("wm_image").strip()) or None) if kind == "image" else None,
            watermark_pos=self.val("wm_pos"),
            watermark_size=float(self.val("wm_size") or 0.12),
            watermark_opacity=float(self.val("wm_opacity") or 0.6),
            watermark_margin=float(self.val("wm_margin") or 0.03),
        )

    def remember(self):
        self.cfg["ui"] = dict(folder=self.val("folder"), root=str(self.query_one("#tree").path),
                              preset=self.val("preset"), bars=int(self.val("bars")),
                              bpm=self.val("bpm"), lufs=self.val("lufs"), cut=self.val("cut"),
                              titles=self.val("titles"))
        self.cfg["brand"] = asdict(self.brand_now())
        config.save(self.cfg)

    def folder(self):
        f = os.path.expanduser(self.val("folder").strip())
        if not os.path.isdir(f):
            self.notify("Pick a folder first", severity="error")
            return None
        return f

    def load_folder(self, path):
        self.query_one("#folder", Input).value = path
        vids = list_videos(path)
        sel = self.query_one("#video", Select)
        opts = [(v, os.path.join(path, v)) for v in vids] + [("Audio only (no video)", AUDIO_ONLY)]
        sel.set_options(opts)
        sel.value = opts[0][1]
        self.query_one("#seed", Input).value = ""
        self.action_analyze()

    # ------------------------------------------------------------ events
    @on(DirectoryTree.DirectorySelected, "#tree")
    def picked(self, event):
        self.load_folder(str(event.path))

    @on(Input.Submitted, "#folder")
    def typed_folder(self, event):
        if os.path.isdir(os.path.expanduser(event.value)):
            self.load_folder(os.path.expanduser(event.value))

    @on(Select.Changed, "#preset")
    def preset_changed(self, event):
        if not getattr(self, "ready", False):
            return
        p = PRESETS[event.value]
        self.query_one("#preset_desc", Static).update(p.description)
        self.query_one("#bars", Select).value = p.bars
        self.query_one("#cut", Switch).value = p.cut_breakdowns
        bpm = self.query_one("#bpm", Input)
        bpm.value, bpm.placeholder = "", f"preset: {p.bpm_center:g}"

    @on(Button.Pressed)
    def button(self, event):
        {"analyze": self.action_analyze, "plan": self.action_plan,
         "render": self.action_render}[event.button.id]()

    def start(self, what):
        if self.busy:
            self.notify("Still working on the last job", severity="warning")
            return False
        try:
            self.settings(), self.brand_now()
        except ValueError as e:
            self.notify(f"Check the numbers in the form: {e}", severity="error")
            return False
        self.busy = True
        self.status(what)
        return True

    def done(self, msg):
        self.busy = False
        self.status(msg)

    # ------------------------------------------------------------ jobs
    def action_analyze(self):
        folder = self.folder()
        if folder and self.start("Analyzing…"):
            self.run_analyze(folder, self.settings())

    @work(thread=True, group="job")
    def run_analyze(self, folder, settings):
        from . import pipeline
        try:
            tracks = pipeline.analyze(folder, settings, log=lambda m: self.call_from_thread(self.log_line, m))
        except Exception as e:
            self.call_from_thread(self.failed, e)
            return
        self.call_from_thread(self.show_tracks, tracks, settings)

    def show_tracks(self, tracks, settings):
        t = self.query_one("#tracks", DataTable)
        t.clear()
        for tr in sorted(tracks, key=lambda x: x["bpm"]):
            bd = "-"
            if tr["clean_end"] < tr["duration"] - 1:
                bd = f"choppy from {fmt_time(tr['clean_end'])}" + ("" if settings.cut_breakdowns else " (kept)")
            t.add_row(tr["file"], tr["song"], f"{tr['bpm']:.1f}", f"{tr['wander']:.2f}",
                      f"{tr['key']} {tr['camelot']}", f"{tr['lufs']:.1f}", fmt_time(tr["duration"]), bd)
        self.query_one("#tabs", TabbedContent).active = "tab-tracks"
        self.done(f"{len(tracks)} tracks analyzed")

    def action_plan(self):
        folder = self.folder()
        if folder and self.start("Planning…"):
            self.remember()
            self.run_plan(folder, self.settings())

    @work(thread=True, group="job")
    def run_plan(self, folder, settings):
        from . import pipeline
        try:
            res = pipeline.plan(folder, settings, log=lambda m: self.call_from_thread(self.log_line, m))
        except Exception as e:
            self.call_from_thread(self.failed, e)
            return
        self.call_from_thread(self.show_plan, *res, settings)

    def show_plan(self, seed, order, steps, length, settings):
        self.query_one("#seed", Input).value = str(seed)   # so Render reproduces this exact plan
        t = self.query_one("#plan_table", DataTable)
        t.clear()
        beat = 0
        for i, tr in enumerate(order):
            if i == 0:
                at, how = "0:00", "—"
            else:
                st = steps[i - 1]
                at = fmt_time(st["at"])
                x = st["tr"]
                if x["kind"] == "beatmatch":
                    beat += 1
                    how = f"beatmatch {x['bars']} bars, {100 * (x['rb'] - 1):+.1f}%"
                else:
                    how = "crossfade"
            t.add_row(str(i + 1), tr["file"], f"{tr['bpm']:.1f}", tr["camelot"], at, how)
        self.query_one("#tabs", TabbedContent).active = "tab-plan"
        self.done(f"Seed {seed}: ~{fmt_time(length)}, {beat}/{len(steps)} transitions beatmatched. "
                  f"Render keeps this order.")

    def action_render(self):
        folder = self.folder()
        if not (folder and self.start("Rendering…")):
            return
        settings = self.settings()
        if settings.seed is None:
            import random
            settings = replace(settings, seed=random.randrange(10 ** 6))
            self.query_one("#seed", Input).value = str(settings.seed)
        self.remember()
        video = self.val("video")
        self.query_one("#tabs", TabbedContent).active = "tab-log"
        self.query_one("#progress", ProgressBar).update(progress=0)
        self.run_render(folder, settings, self.brand_now(), self.val("titles"),
                        None if video in (AUDIO_ONLY, Select.BLANK) else video,
                        video == AUDIO_ONLY)

    @work(thread=True, group="job")
    def run_render(self, folder, settings, brand, titles, video, audio_only):
        from . import pipeline
        bar = self.query_one("#progress", ProgressBar)
        try:
            res = pipeline.mix(folder, settings, brand=brand, titles=titles, video=video,
                               audio_only=audio_only or video is None,
                               log=lambda m: self.call_from_thread(self.log_line, m),
                               progress=lambda f: self.call_from_thread(bar.update, progress=round(100 * f)))
        except Exception as e:
            self.call_from_thread(self.failed, e)
            return
        out = res.get("video") or res.get("audio")
        self.call_from_thread(bar.update, progress=100)
        self.call_from_thread(self.done, f"Done: {os.path.basename(out)} ({fmt_time(res['duration'])})")
        self.call_from_thread(self.notify, f"Saved {out}", timeout=10)

    def failed(self, e):
        self.log_line(f"[red]Error: {e}")
        self.done(f"[red]Failed: {e}")
        self.notify(str(e), severity="error", timeout=10)


def run(folder=None):
    DJMix(os.path.abspath(os.path.expanduser(folder)) if folder else None).run()
