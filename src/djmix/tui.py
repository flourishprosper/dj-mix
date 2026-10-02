"""Terminal interface: pick a folder, set options, preview the plan, render."""
import os, subprocess, sys, time
from dataclasses import asdict, replace
from pathlib import Path

from textual import on, work
from textual.binding import Binding
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import OptionList
from textual.widgets.option_list import Option
from textual.widgets import (Button, DataTable, DirectoryTree, Footer, Header, Input, Label,
                             ProgressBar, RichLog, Select, Static, Switch, TabbedContent, TabPane)

from . import config, history
from .brand import POSITIONS, Brand
from .presets import DEFAULT_PRESET, PRESETS, Settings
from .logstyle import LogHighlighter
from .util import display_title, fmt_time, list_videos, missing_tools

AUDIO_ONLY = "__audio_only__"


class FolderTree(DirectoryTree):
    # Space is play/pause everywhere; the tree would otherwise use it to fold a folder
    BINDINGS = [("space", "app.play_pause", "Play/Pause")]

    class Menu(Message):
        """Right-click on a folder: show the folder menu at the mouse."""
        def __init__(self, path, x, y):
            super().__init__()
            self.path, self.x, self.y = path, x, y

    def filter_paths(self, paths):
        return [p for p in paths if p.is_dir() and not p.name.startswith((".", "_", "MIX_"))]

    async def _on_click(self, event):
        # Textual also runs Tree's own click handler (it opens the folder) unless
        # the default is prevented, so left-clicks just fall through to it.
        if event.button != 3:
            return
        event.prevent_default()                   # right-click: menu, don't open the folder
        event.stop()
        line = event.style.meta.get("line")
        node = self.get_node_at_line(line) if line is not None else self.cursor_node
        if node is not None and node.data is not None:
            self.cursor_line = node.line
            self.post_message(self.Menu(str(node.data.path), event.screen_x, event.screen_y))


FILE_MANAGER = "Finder" if sys.platform == "darwin" else "Explorer" if os.name == "nt" else "file manager"


def reveal(path):
    """Open a folder in Finder (or the system file manager)."""
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])
    elif os.name == "nt":
        os.startfile(path)
    else:
        subprocess.Popen(["xdg-open", path])


class ContextMenu(ModalScreen):
    """A small pop-up menu at the mouse position. Dismisses with the chosen id."""
    DEFAULT_CSS = """
    ContextMenu { background: transparent; }
    ContextMenu OptionList { width: 30; height: auto; max-height: 12; border: round $accent;
                             background: $panel; padding: 0; }
    """
    BINDINGS = [("escape", "dismiss(None)", "Close")]

    def __init__(self, title, items, x, y):
        super().__init__()
        self.title_text, self.items, self.at = title, items, (x, y)

    def compose(self):
        menu = OptionList(*[Option(label, id=key) for label, key in self.items], id="menu")
        menu.border_title = self.title_text
        yield menu

    def on_mount(self):
        menu = self.query_one(OptionList)
        x, y = self.at
        w, h = 30, len(self.items) + 2
        menu.styles.offset = (max(0, min(x, self.size.width - w)), max(0, min(y, self.size.height - h)))
        menu.focus()

    def on_option_list_option_selected(self, event):
        self.dismiss(event.option.id)

    def on_click(self, event):
        if not self.query_one(OptionList).region.contains(event.screen_x, event.screen_y):
            self.dismiss(None)                    # clicked outside the menu


COLOR_FIELDS = ("accent", "text_color")


def parse_color(value):
    """Same parser the renderer uses, so the swatch matches the video. None if invalid."""
    from PIL import ImageColor
    try:
        return ImageColor.getrgb(value.strip())[:3]
    except ValueError:
        return None


def color_row(label, widget):
    return Horizontal(Label(label, classes="lbl"), widget,
                      Static("", id=f"{widget.id}_swatch", classes="swatch"), classes="row")


class PlayTable(DataTable):
    """DataTable where double-click plays the row (see DJMix.play_row)."""

    class Play(Message):
        def __init__(self, table, row_key):
            super().__init__()
            self.table, self.row_key = table, row_key

    click_at = 0.0     # time of the last mouse click on this table
    double_at = -1.0   # time of the last double-click

    def on_click(self, event):
        self.click_at = time.monotonic()
        if event.chain >= 2 and self.row_count:
            self.double_at = self.click_at
            key = self.coordinate_to_cell_key(self.cursor_coordinate).row_key
            self.post_message(self.Play(self, key))


def row(label, widget):
    return Horizontal(Label(label, classes="lbl"), widget, classes="row")


class DJMix(App):
    TITLE = "dj-mix"
    CSS = """
    #left { width: 56; border-right: solid $panel; }
    #browser { height: 12; border: round $panel; border-title-color: $text-muted; margin: 0 1; }
    #options { height: 1fr; padding: 0 1; }
    .section { margin: 1 0 0 0; color: $accent; text-style: bold; }
    .row { height: auto; }
    .row .lbl { width: 20; padding: 1 1 0 0; color: $text-muted; }
    .row Input, .row Select { width: 1fr; }
    .row Switch { margin: 0 0 0 0; }
    .swatch { width: 6; height: 3; margin: 0 0 0 1; border: tall $panel; content-align: center middle; }
    .swatch.bad { border: tall $error; color: $error; }
    #buttons { height: auto; margin: 1 0; }
    #buttons Button { width: 1fr; margin: 0 1 0 0; }
    #status { height: 1; padding: 0 1; color: $text-muted; }
    #progress { padding: 0 1; }
    #tabs { height: 1fr; }
    #preset_desc { padding: 0 0 0 20; color: $text-muted; }
    DataTable { height: 1fr; }
    #promo_bar { height: auto; padding: 0 0 1 0; }
    #promo_bar Select { width: 44; }
    #promo_bar Input { width: 8; }
    #promo_bar #promo_fmt { width: 22; }
    #promo_bar Label { padding: 1 1 0 1; color: $text-muted; }
    #promo_bar Button { margin: 0 0 0 1; min-width: 10; }
    #promo_hint, #history_hint { color: $text-muted; padding: 0 0 1 0; }
    #player { height: 3; border-top: solid $panel; padding: 0 1; }
    #player .pl { min-width: 5; width: 5; margin: 0 1 0 0; }
    #pl_now { width: 1fr; padding: 1 1 0 1; }
    #pl_vol { width: 5; padding: 1 0 0 0; content-align: center top; }
    #refresh { min-width: 5; width: 5; margin: 0 0 0 1; }
    """
    BINDINGS = [("a", "analyze", "Analyze"), ("p", "plan", "Plan"),
                ("r", "render", "Render"), ("e", "export", "Export promos"), ("f5", "refresh", "Refresh"), ("q", "quit", "Quit"),
                Binding("c", "convert", "Convert to MP3", show=False),
                ("space", "play_pause", "Play/Pause"), ("[", "seek(-10)", "−10s"), ("]", "seek(10)", "+10s"),
                ("minus", "volume(-0.1)", "Vol −"), ("equals_sign", "volume(0.1)", "Vol +")]

    def __init__(self, folder=None):
        # Analysis uses a process pool. Its helper process is handed the real
        # stderr, which Textual replaces once the app runs (fileno() = -1 ->
        # "bad value(s) in fds_to_keep"). Start the helper now, before that.
        from multiprocessing import resource_tracker
        resource_tracker.ensure_running()
        super().__init__()
        self.cfg = config.load()
        self.ui = self.cfg.get("ui", {})
        self.brand = config.brand_defaults()
        self.start_folder = folder or self.ui.get("folder", "")
        self.busy = False
        self.logs = {}                       # folder -> this session's log lines
        self.loghl = LogHighlighter()        # colors for the Log tab
        from .player import Player
        self.player = Player()

    # ------------------------------------------------------------ layout
    def compose(self) -> ComposeResult:
        ui, b = self.ui, self.brand
        root = self.ui.get("root") or (str(Path(self.start_folder).parent) if self.start_folder else os.getcwd())
        preset = ui.get("preset", DEFAULT_PRESET)
        yield Header()
        with Horizontal():
            with Vertical(id="left"):
                tree = FolderTree(root, id="browser")
                tree.border_title = "Music folders · Enter opens · right-click: menu"
                yield tree
                with VerticalScroll(id="options"):
                    yield Horizontal(Label("Folder", classes="lbl"),
                                     Input(self.start_folder, id="folder", placeholder="path to a folder of tracks"),
                                     Button("⟳", id="refresh", tooltip="Refresh folders and files (F5)"),
                                     classes="row")

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
                    yield color_row("Accent color", Input(b.accent, id="accent"))
                    yield color_row("Text color", Input(b.text_color, id="text_color"))
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
                        yield PlayTable(id="tracks", zebra_stripes=True, cursor_type="row")
                    with TabPane("Plan", id="tab-plan"):
                        yield PlayTable(id="plan_table", zebra_stripes=True, cursor_type="row")
                    with TabPane("Promo", id="tab-promo"):
                        yield Static("Short clips of single songs, cut from a finished mix video. "
                                     "Enter/click toggles a song ✓, then Export (e).", id="promo_hint")
                        with Horizontal(id="promo_bar"):
                            yield Select([], id="promo_render", prompt="(render a mix first)")
                            yield Label("Seconds")
                            yield Input("30", id="promo_len", type="number")
                            yield Select([("Vertical 9:16", "vertical"), ("Square 1:1", "square"),
                                          ("Original 16:9", "original")], value="vertical",
                                         allow_blank=False, id="promo_fmt")
                            yield Label("Title card")
                            yield Switch(True, id="promo_card")
                            yield Button("All", id="promo_all")
                            yield Button("Export [e]", id="promo_export", variant="success")
                        yield PlayTable(id="promo_table", zebra_stripes=True, cursor_type="row")
                    with TabPane("History", id="tab-history"):
                        yield Static("Every render made in this folder. Enter/click a row to load its "
                                     "settings (and pick it for promos).", id="history_hint")
                        yield PlayTable(id="history_table", zebra_stripes=True, cursor_type="row")
                    with TabPane("Log", id="tab-log"):
                        yield RichLog(id="log", wrap=True, markup=True)
                with Horizontal(id="player"):
                    yield Button("⏪", id="pl_back", classes="pl")
                    yield Button("▶", id="pl_toggle", classes="pl")
                    yield Button("⏩", id="pl_fwd", classes="pl")
                    yield Button("⏹", id="pl_stop", classes="pl")
                    yield Static("Double-click a song, mix or promo row to listen", id="pl_now")
                    yield Button("−", id="pl_vdown", classes="pl")
                    yield Static("80%", id="pl_vol")
                    yield Button("+", id="pl_vup", classes="pl")
                yield ProgressBar(id="progress", total=100, show_eta=False)
                yield Static("", id="status")
        yield Footer()

    def on_mount(self):
        self.query_one("#tracks", DataTable).add_columns(
            "Track", "Song", "BPM", "Drift", "Key", "LUFS", "Length", "Breakdown")
        self.query_one("#plan_table", DataTable).add_columns(
            "#", "Track", "BPM", "Key", "In at", "Transition in")
        self.query_one("#promo_table", DataTable).add_columns(
            "✓", "#", "Song", "Clip (time in the mix)", "Plays alone", "Exported")
        self.query_one("#history_table", DataTable).add_columns(
            "When", "Output", "Length", "Seed", "Preset", "Beatmatched", "Render time", "Promos", "")
        self.promo_sel, self.renders, self.clips = set(), [], {}
        self.set_interval(0.25, self.player_tick)
        self.skip_preset_change = None
        self.ready = False
        for wid in COLOR_FIELDS:
            self.update_swatch(wid)
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

    def log_line(self, msg, folder=None):
        """Log output belongs to a folder; the Log tab only shows the open folder's."""
        folder = folder or getattr(self, "current_folder", "")
        self.logs.setdefault(folder, []).append(msg)
        if folder == getattr(self, "current_folder", ""):
            self.query_one("#log", RichLog).write(self.styled(msg))

    def styled(self, msg):
        """Plain text (brackets are content, not markup) + log colors."""
        from rich.text import Text
        t = Text(str(msg))
        self.loghl.highlight(t)
        return t

    def update_log_names(self, folder):
        """Track/file names in the folder, so the log can color them exactly."""
        try:
            files = [f for f in os.listdir(folder) if not f.startswith(".")]
        except OSError:
            files = []
        self.loghl.set_names(files + [display_title(f) for f in files])

    def show_folder_log(self, folder):
        """Log tab = this folder's saved diary + this session's output here, colored."""
        from rich.text import Text
        log = self.query_one("#log", RichLog)
        log.clear()
        self.update_log_names(folder)
        log.write(Text(f"Log — {os.path.basename(folder)}", style="bold"))
        try:
            with open(os.path.join(folder, history.DIRNAME, "log.txt")) as f:
                diary = f.read().splitlines()[-300:]
        except OSError:
            diary = []
        if diary:
            log.write(Text("── earlier in this folder ──", style="dim"))
            for line in diary:
                log.write(self.styled(line))
        session = self.logs.get(folder, [])
        if session:
            log.write(Text("── this session ──", style="dim"))
            for line in session:
                log.write(self.styled(line))
        if not diary and not session:
            log.write(Text("No saved history in this folder yet.", style="dim"))

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
        self.cfg["ui"] = dict(folder=self.val("folder"), root=str(self.query_one("#browser").path),
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
        """Open a folder and pick up where we left off there."""
        if self.busy and path == getattr(self, "current_folder", None):
            return                                    # e.g. the 2nd click of a double-click
        if self.busy:
            self.notify("Wait for the current job to finish before switching folders", severity="warning")
            return
        self.current_folder = path
        self.show_folder_log(path)
        self.query_one("#folder", Input).value = path
        self.refresh_videos(path)
        sel = self.query_one("#video", Select)
        self.query_one("#seed", Input).value = ""
        self.resume = None
        h = history.load(path)
        last = dict(h["last"])
        if last.get("seed") is None:
            # nothing planned here yet this way: pick up from the newest render instead
            newest = max(h["renders"], key=lambda r: r.get("created", ""), default=None)
            if newest and newest.get("seed") is not None:
                last = {**{k: newest.get(k) for k in ("seed", "preset", "bars", "bpm", "lufs", "cut", "titles")},
                        **{k: v for k, v in last.items() if v is not None}}
        if last:
            self.apply_settings(last)
            if last.get("video") and os.path.exists(os.path.join(path, last["video"])):
                sel.value = os.path.join(path, last["video"])
            if "titles" in last:
                self.query_one("#titles", Switch).value = bool(last["titles"])
            for wid, key in (("promo_len", "promo_len"), ("promo_fmt", "promo_fmt"), ("promo_card", "promo_card")):
                if key in last:
                    self.query_one(f"#{wid}").value = last[key] if wid != "promo_len" else str(last[key])
            self.resume = last.get("seed")
        self.promo_sel = set()
        self.action_analyze()

    def resume_from_newest(self, folder):
        """First open of a folder that already had mixes: pick up from the newest one."""
        if self.resume is not None or history.load(folder)["last"].get("seed") is not None:
            return
        newest = max(history.load(folder)["renders"], key=lambda r: r.get("created", ""), default=None)
        if newest and newest.get("seed") is not None:
            self.apply_settings(newest)
            self.resume = newest["seed"]

    def refresh_videos(self, path, keep=None):
        """Fill the Video list from the folder, keeping `keep` selected if it's still there."""
        vids = list_videos(path)
        sel = self.query_one("#video", Select)
        opts = [(v, os.path.join(path, v)) for v in vids] + [("Audio only (no video)", AUDIO_ONLY)]
        sel.set_options(opts)
        values = [v for _, v in opts]
        sel.value = keep if keep in values else values[0]

    def apply_settings(self, r):
        """Put saved mix settings (from history) into the form."""
        preset = r.get("preset") or DEFAULT_PRESET
        if preset in PRESETS and preset != self.val("preset"):
            self.skip_preset_change = preset
            self.query_one("#preset", Select).value = preset
            self.query_one("#preset_desc", Static).update(PRESETS[preset].description)
        if r.get("bars"):
            self.query_one("#bars", Select).value = int(r["bars"])
        bpm = self.query_one("#bpm", Input)
        bpm.value = "" if r.get("bpm") in (None, "") else f"{float(r['bpm']):g}"
        bpm.placeholder = f"preset: {PRESETS.get(preset, PRESETS[DEFAULT_PRESET]).bpm_center:g}"
        if r.get("lufs") is not None:
            self.query_one("#lufs", Input).value = str(r["lufs"])
        if r.get("cut") is not None:
            self.query_one("#cut", Switch).value = bool(r["cut"])
        self.query_one("#seed", Input).value = "" if r.get("seed") is None else str(r["seed"])

    # ------------------------------------------------------------ events
    def update_swatch(self, wid):
        from textual.color import Color
        sw = self.query_one(f"#{wid}_swatch", Static)
        rgb = parse_color(self.val(wid))
        sw.set_class(rgb is None, "bad")
        sw.styles.background = Color(*rgb) if rgb else None
        sw.update("" if rgb else "?")

    @on(Input.Changed)
    def color_typed(self, event):
        if event.input.id in COLOR_FIELDS:
            self.update_swatch(event.input.id)

    @on(DirectoryTree.DirectorySelected, "#browser")
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
        if self.skip_preset_change == event.value:      # set by a restore, keep restored values
            self.skip_preset_change = None
            return
        p = PRESETS[event.value]
        self.query_one("#preset_desc", Static).update(p.description)
        self.query_one("#bars", Select).value = p.bars
        self.query_one("#cut", Switch).value = p.cut_breakdowns
        bpm = self.query_one("#bpm", Input)
        bpm.value, bpm.placeholder = "", f"preset: {p.bpm_center:g}"

    @on(Button.Pressed)
    def button(self, event):
        fn = {"analyze": self.action_analyze, "plan": self.action_plan, "render": self.action_render,
              "refresh": self.action_refresh,
              "promo_export": self.action_export, "promo_all": self.promo_all}.get(event.button.id)
        if fn:
            fn()

    def start(self, what):
        if self.busy:
            self.notify("Still working on the last job", severity="warning")
            return False
        bad = [w.replace("_", " ") for w in COLOR_FIELDS if parse_color(self.val(w)) is None]
        if bad:
            self.notify(f"Not a color: {', '.join(bad)} (try #F2A65A or 'orange')", severity="error")
            return False
        try:
            self.settings(), self.brand_now()
        except ValueError as e:
            self.notify(f"Check the numbers in the form: {e}", severity="error")
            return False
        self.busy = True
        self.job, self.job_start, self.job_frac = what, time.monotonic(), 0.0
        self.status(what)
        self.ticker = self.set_interval(1, self.tick)
        return True

    def elapsed(self):
        return time.monotonic() - self.job_start

    def tick(self):
        """Running clock while a job works: elapsed, and for renders % + time left."""
        el = self.elapsed()
        msg = f"{self.job}  [b]{fmt_time(el)}[/b] elapsed"
        if self.job_frac > 0.02:
            msg += f"  ·  {self.job_frac:.0%}  ·  ~{fmt_time(el / self.job_frac - el)} left"
        self.status(msg)

    def set_frac(self, f):
        self.job_frac = f
        self.query_one("#progress", ProgressBar).update(progress=round(100 * f))

    def done(self, msg):
        self.busy = False
        self.ticker.stop()
        self.status(msg)

    # ------------------------------------------------------------ jobs
    def action_analyze(self):
        folder = self.folder()
        if folder and self.start("Analyzing…"):
            self.run_analyze(folder, self.settings())

    @work(thread=True, group="job")
    def run_analyze(self, folder, settings, switch_tab=True):
        from . import pipeline
        log = lambda m: self.call_from_thread(self.log_line, m, folder)
        try:
            tracks = pipeline.analyze(folder, settings, log=log)
            adopted = pipeline.adopt_legacy(folder, log=log)
        except Exception as e:
            self.call_from_thread(self.failed, e)
            return
        if adopted:
            self.call_from_thread(self.resume_from_newest, folder)
        self.call_from_thread(self.show_tracks, tracks, settings, switch_tab)
        self.call_from_thread(self.refresh_history, folder)

    def show_tracks(self, tracks, settings, switch_tab=True):
        t = self.query_one("#tracks", DataTable)
        t.clear()
        for tr in sorted(tracks, key=lambda x: x["bpm"]):
            bd = "-"
            if tr["clean_end"] < tr["duration"] - 1:
                bd = f"choppy from {fmt_time(tr['clean_end'])}" + ("" if settings.cut_breakdowns else " (kept)")
            t.add_row(display_title(tr["file"]), tr["song"], f"{tr['bpm']:.1f}", f"{tr['wander']:.2f}",
                      f"{tr['key']} {tr['camelot']}", f"{tr['lufs']:.1f}", fmt_time(tr["duration"]), bd,
                      key=tr["file"])
        before = getattr(self, "track_count", None)
        self.track_count = len(tracks)
        self.update_log_names(getattr(self, "current_folder", ""))
        self.warn_convertible()
        if switch_tab:
            self.query_one("#tabs", TabbedContent).active = "tab-tracks"
            self.done(f"{len(tracks)} tracks analyzed")
        else:
            new = "" if before is None else f" ({len(tracks) - before:+d})" if len(tracks) != before else ""
            self.done(f"Refreshed: {len(tracks)} tracks{new}")
        if getattr(self, "resume", None) is not None:      # reopen where we left off: show that plan
            self.resume = None
            self.call_after_refresh(self.action_plan)

    def action_plan(self):
        folder = self.folder()
        if folder and self.start("Planning…"):
            self.remember()
            self.run_plan(folder, self.settings(), self.val("titles"))

    @work(thread=True, group="job")
    def run_plan(self, folder, settings, titles):
        from . import pipeline
        try:
            res = pipeline.plan(folder, settings, log=lambda m: self.call_from_thread(self.log_line, m, folder))
            history.remember_settings(folder, **pipeline.settings_record(settings, res[0]),
                                      titles=titles)
            history.log(folder, f"plan  seed {res[0]}, preset {settings.preset.name}, {settings.bars} bars, "
                                f"~{fmt_time(res[3])}")
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
            t.add_row(str(i + 1), display_title(tr["file"]), f"{tr['bpm']:.1f}", tr["camelot"], at, how,
                      key=tr["file"])
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
                               log=lambda m: self.call_from_thread(self.log_line, m, folder),
                               progress=lambda f: self.call_from_thread(self.set_frac, f))
        except Exception as e:
            self.call_from_thread(self.failed, e)
            return
        out = res.get("video") or res.get("audio")
        self.call_from_thread(bar.update, progress=100)
        self.call_from_thread(self.done, f"Done: {os.path.basename(out)} ({fmt_time(res['duration'])} mix) "
                                         f"— rendered in [b]{fmt_time(res['elapsed'])}[/b]")
        self.call_from_thread(self.notify, f"Saved {os.path.basename(out)} in {os.path.basename(folder)}", timeout=10)
        self.call_from_thread(self.refresh_history, folder, res["record"]["id"])

    # ------------------------------------------------------------ history + promo
    def refresh_history(self, folder, select=None):
        """Reload the History and Promo lists. select: render id to pick for promos
        (a render that just finished); otherwise keep the current pick."""
        self.renders = history.renders(folder)
        t = self.query_one("#history_table", DataTable)
        t.clear()
        for r in self.renders:
            out = r.get("video") or r.get("audio") or "?"
            state = "" if r["exists"] else "file missing"
            if r["exists"] and not r.get("timeline"):
                state = r.get("note", "no timeline")
            t.add_row(r.get("created", ""), out, fmt_time(r["duration"]) if r.get("duration") else "",
                      str(r.get("seed", "")), f"{r.get('preset', '')} {r.get('bars') or ''}".strip(),
                      f"{r['beatmatched']}/{r['transitions']}" if "beatmatched" in r else "",
                      fmt_time(r["elapsed"]) if r.get("elapsed") else ("earlier" if r.get("legacy") else ""),
                      str(len(r.get("promos", []))) or "", state, key=r["id"])
        cuttable = [r for r in self.renders if r["exists"] and r.get("video") and r.get("timeline")]
        sel = self.query_one("#promo_render", Select)
        current = select or sel.value
        sel.set_options([(f"{r['created']} · {r['video']} · {fmt_time(r.get('duration', 0))}", r["id"])
                         for r in cuttable])
        if cuttable:
            ids = [r["id"] for r in cuttable]
            sel.value = current if current in ids else ids[0]
        else:
            self.query_one("#promo_table", DataTable).clear()

    def render_by_id(self, rid):
        return next((r for r in self.renders if r["id"] == rid), None)

    @on(Select.Changed, "#promo_render")
    def promo_render_changed(self, event):
        self.promo_sel = set()
        self.fill_promo_table()

    @on(Input.Changed, "#promo_len")
    def promo_len_changed(self, event):
        try:
            if float(event.value) >= 5:
                self.fill_promo_table()          # clip times depend on the length
        except ValueError:
            pass

    def promo_length(self):
        try:
            return max(5.0, float(self.val("promo_len") or 30))
        except ValueError:
            return 30.0

    def fill_promo_table(self):
        r = self.render_by_id(self.val("promo_render"))
        t = self.query_one("#promo_table", DataTable)
        t.clear()
        if not r:
            return
        done = {}
        for p in r.get("promos", []):
            done.setdefault(p["song"], set()).add(p["format"])
        for i, song in enumerate(r["timeline"]):
            t.add_row("✓" if i in self.promo_sel else "", str(i + 1), song["title"], "…",
                      f"{fmt_time(song['full_in'])}–{fmt_time(song['out_start'])}",
                      ", ".join(sorted(done.get(i, []))), key=str(i))
        self.compute_clips(getattr(self, "current_folder", ""), r, self.promo_length())

    @work(thread=True, group="clips", exclusive=True)
    def compute_clips(self, folder, r, length):
        """Work out where each song's clip will be cut (shown before exporting)."""
        from . import promo
        video = os.path.join(folder, r["video"])
        for i, song in enumerate(r["timeline"]):
            try:
                start, L, clean = promo.choose_clip(video, song, length, r.get("titles", True))
                txt = f"{fmt_time(start)}–{fmt_time(start + L)}" + ("" if clean else "  (overlaps a transition)")
            except Exception as e:
                txt = f"error: {e}"
            self.call_from_thread(self.set_clip_cell, r["id"], i, txt)

    def set_clip_cell(self, rid, i, txt):
        if self.val("promo_render") != rid:
            return
        t = self.query_one("#promo_table", DataTable)
        try:
            t.update_cell(str(i), t.ordered_columns[3].key, txt)
        except Exception:
            pass

    def unless_double(self, table, fn):
        """Run fn for a row selection, but if it came from a mouse click, wait
        briefly: a second click makes it a double-click (play) instead."""
        ts = table.click_at
        if table.double_at == ts:                   # this selection IS the double-click
            return
        if time.monotonic() - ts < 0.1:             # from a click: wait for a possible 2nd
            self.set_timer(0.35, lambda: fn() if table.double_at < ts else None)
        else:                                       # keyboard: act now
            fn()

    @on(DataTable.RowSelected, "#promo_table")
    def promo_toggle_event(self, event):
        self.unless_double(event.data_table, lambda k=event.row_key: self.promo_toggle(k))

    def promo_toggle(self, row_key):
        i = int(row_key.value)
        self.promo_sel ^= {i}
        t = self.query_one("#promo_table", DataTable)
        t.update_cell(row_key, t.ordered_columns[0].key, "✓" if i in self.promo_sel else "")

    def promo_all(self):
        r = self.render_by_id(self.val("promo_render"))
        if not r:
            return
        allsel = set(range(len(r["timeline"])))
        self.promo_sel = set() if self.promo_sel == allsel else allsel
        t = self.query_one("#promo_table", DataTable)
        for i in allsel:
            t.update_cell(str(i), t.ordered_columns[0].key, "✓" if i in self.promo_sel else "")

    @on(DataTable.RowSelected, "#history_table")
    def history_pick_event(self, event):
        self.unless_double(event.data_table, lambda k=event.row_key: self.history_pick(k))

    def history_pick(self, row_key):
        r = self.render_by_id(row_key.value)
        if not r:
            return
        self.apply_settings(r)
        if r.get("titles") is not None:
            self.query_one("#titles", Switch).value = bool(r["titles"])
        if r["exists"] and r.get("timeline") and r.get("video"):
            self.query_one("#promo_render", Select).value = r["id"]
        self.notify(f"Loaded settings from {r.get('video') or r.get('audio')} (seed {r.get('seed')}). "
                    f"Press p to see its plan, or open Promo to cut clips.", timeout=8)

    def action_export(self):
        folder = self.folder()
        r = self.render_by_id(self.val("promo_render"))
        if not folder:
            return
        if not r:
            self.notify("Render a mix first — promo clips are cut from the finished video", severity="warning")
            return
        if not self.promo_sel:
            self.notify("Tick the songs you want (Enter/click a row, or All)", severity="warning")
            self.query_one("#tabs", TabbedContent).active = "tab-promo"
            return
        if not self.start("Exporting promos…"):
            return
        length, fmt, card = self.promo_length(), self.val("promo_fmt"), self.val("promo_card")
        history.remember_settings(folder, promo_len=length, promo_fmt=fmt, promo_card=card)
        self.query_one("#progress", ProgressBar).update(progress=0)
        self.log_line(f"Exporting {len(self.promo_sel)} promo clip(s), {fmt}, {length:g}s, from {r['video']}")
        self.run_export(folder, r, sorted(self.promo_sel), length, fmt, card, self.brand_now())

    @work(thread=True, group="job")
    def run_export(self, folder, r, songs, length, fmt, card, brand):
        from . import promo
        try:
            outs = promo.export(folder, r, songs, length, fmt, card, brand,
                                log=lambda m: self.call_from_thread(self.log_line, m, folder),
                                progress=lambda f: self.call_from_thread(self.set_frac, f))
        except Exception as e:
            self.call_from_thread(self.failed, e)
            return
        where = os.path.basename(os.path.dirname(outs[0])) if outs else ""
        self.call_from_thread(self.done, f"Exported {len(outs)} promo clip(s) to {where}/ "
                                         f"in [b]{fmt_time(self.elapsed())}[/b]")
        self.call_from_thread(self.refresh_history, folder)
        self.call_from_thread(self.fill_promo_table)

    # ------------------------------------------------------------ converting to mp3
    def warn_convertible(self, folder=None):
        from .convert import convertible, summary
        folder = folder or getattr(self, "current_folder", None)
        todo = convertible(folder) if folder else []
        if todo:
            self.notify(f"{summary(todo)} in this folder can't be mixed yet. Press c to convert "
                        f"{'it' if len(todo) == 1 else 'them'} to MP3 (originals are kept).",
                        title="Files need converting", severity="warning", timeout=15)
        return todo

    def action_convert(self, folder=None):
        from .convert import convertible, summary
        folder = folder or self.folder()
        if not folder:
            return
        todo = convertible(folder)
        if not todo:
            self.notify("Nothing to convert here: every audio file is already MP3/WAV/FLAC/AIFF")
            return
        if self.start(f"Converting {summary(todo)} to MP3…"):
            self.query_one("#tabs", TabbedContent).active = "tab-log"
            self.query_one("#progress", ProgressBar).update(progress=0)
            self.run_convert(folder)

    @work(thread=True, group="job")
    def run_convert(self, folder):
        from . import history
        from .convert import convert_folder
        try:
            res = convert_folder(folder, log=lambda m: self.call_from_thread(self.log_line, m, folder),
                                 progress=lambda f: self.call_from_thread(self.set_frac, f))
        except Exception as e:
            self.call_from_thread(self.failed, e)
            return
        ok = sum(r == "ok" for r in res.values())
        history.log(folder, f"converted {ok}/{len(res)} file(s) to MP3 (originals in _converted-originals/)")
        self.call_from_thread(self.done, f"Converted {ok}/{len(res)} file(s) to MP3 in "
                                         f"[b]{fmt_time(self.elapsed())}[/b]")
        if folder == getattr(self, "current_folder", None):
            self.call_from_thread(self.after_convert, folder)

    def after_convert(self, folder):
        if self.start("Analyzing…"):                  # pick up the new MP3s
            self.run_analyze(folder, self.settings())

    # ------------------------------------------------------------ refresh + folder menu
    async def action_refresh(self):
        """Re-read the folder browser and the open folder (new tracks, videos, renders)."""
        tree = self.query_one("#browser", FolderTree)
        await tree.reload()
        folder = getattr(self, "current_folder", None)
        if not folder or not os.path.isdir(folder):
            self.notify("Folder list refreshed")
            return
        if self.busy:
            self.notify("Folder list refreshed; the open folder will refresh when the current job is done")
            return
        self.refresh_videos(folder, keep=self.val("video"))
        if self.start("Refreshing…"):
            self.run_analyze(folder, self.settings(), False)

    @on(FolderTree.Menu)
    def folder_menu(self, event):
        path = event.path
        from .convert import convertible
        items = [(f"Open in {FILE_MANAGER}", "reveal"), ("Open in dj-mix", "open"), ("Refresh", "refresh")]
        n = len(convertible(path))
        if n:
            items.append((f"Convert {n} file{'s' if n != 1 else ''} to MP3", "convert"))
        self.push_screen(ContextMenu(os.path.basename(path) or path, items, event.x, event.y),
                         lambda choice: self.folder_menu_choice(choice, path))

    def folder_menu_choice(self, choice, path):
        if choice == "reveal":
            try:
                reveal(path)
            except Exception as e:
                self.notify(f"Couldn't open {FILE_MANAGER}: {e}", severity="error")
        elif choice == "open":
            self.load_folder(path)
        elif choice == "refresh":
            self.run_worker(self.action_refresh(), exclusive=False)
        elif choice == "convert":
            self.action_convert(path)

    # ------------------------------------------------------------ mini player
    @on(DataTable.RowSelected, "#tracks")
    @on(DataTable.RowSelected, "#plan_table")
    def enter_plays(self, event):
        """Tracks / Plan: Enter plays the song (mouse: only a double-click plays)."""
        if time.monotonic() - event.data_table.click_at > 0.1:
            self.play_row(event.data_table.id, event.row_key)

    @on(PlayTable.Play)
    def double_click_plays(self, event):
        self.play_row(event.table.id, event.row_key)

    def play_row(self, table_id, row_key):
        folder = getattr(self, "current_folder", "")
        if table_id in ("tracks", "plan_table"):
            f = row_key.value
            self.player.play(os.path.join(folder, f), display_title(f))
        elif table_id == "history_table":
            r = self.render_by_id(row_key.value)
            out = r and (r.get("video") or r.get("audio"))
            if not (r and r["exists"]):
                self.notify("That render's file isn't in the folder any more", severity="warning")
                return
            self.player.play(os.path.join(folder, out), f"Mix · {out}")
        elif table_id == "promo_table":
            r = self.render_by_id(self.val("promo_render"))
            if r:
                self.preview_clip(folder, r, int(row_key.value), self.promo_length())
        self.player_tick()

    @work(thread=True, group="clip-preview", exclusive=True)
    def preview_clip(self, folder, r, i, length):
        """Play exactly the part of the mix a promo clip would use."""
        from . import promo
        song = r["timeline"][i]
        video = os.path.join(folder, r["video"])
        start, L, _ = promo.choose_clip(video, song, length, r.get("titles", True))
        self.call_from_thread(self.player.play, video, f"Promo · {song['title']}", start, start + L)

    def player_tick(self):
        p = self.player
        if p._stream and p.finished:
            p.stop()                                  # reached the end (keeps the file for replay)
        self.query_one("#pl_toggle", Button).label = "⏸" if p.playing else "▶"
        self.query_one("#pl_vol", Static).update(f"{round(p.volume * 100)}%")
        if not p.path:
            return
        lo = p._clip_start if p._end is not None else 0.0
        hi = max(p.length, lo + 0.01)
        pos = min(max(p.position if not p.finished else hi, lo), hi)
        from rich.markup import escape
        state = "paused · " if p.paused else ("done · " if p.finished else "")
        times = f"{fmt_time(pos - lo)}  {fmt_time(hi - lo)}"
        room = max(20, self.query_one("#pl_now", Static).size.width - 2)
        title = p.title if len(p.title) <= room // 2 else p.title[:room // 2 - 1] + "…"
        width = max(6, room - len(state) - len(title) - len(times) - 4)   # the bar gets what's left
        fill = min(width, round(width * (pos - lo) / (hi - lo)))
        bar = "━" * fill + "●" + "─" * (width - fill)
        t0, t1 = times.split("  ")
        self.query_one("#pl_now", Static).update(
            f"{state}[b]{escape(title)}[/b]  {t0} {bar} {t1}")

    def action_play_pause(self):
        if not self.player.path:
            self.notify("Double-click a song to play it")
            return
        self.player.toggle()
        self.player_tick()

    def action_seek(self, seconds):
        self.player.seek(float(seconds))
        self.player_tick()

    def action_volume(self, delta):
        self.player.set_volume(self.player.volume + float(delta))
        self.player_tick()

    @on(Button.Pressed, ".pl")
    def player_button(self, event):
        event.stop()
        {"pl_back": lambda: self.action_seek(-10), "pl_toggle": self.action_play_pause,
         "pl_fwd": lambda: self.action_seek(10), "pl_stop": self.player_stop,
         "pl_vdown": lambda: self.action_volume(-0.1), "pl_vup": lambda: self.action_volume(0.1)}[event.button.id]()

    def player_stop(self):
        self.player.stop()
        self.player.path = None
        self.query_one("#pl_now", Static).update("Double-click a song, mix or promo row to listen")
        self.player_tick()

    def on_unmount(self):
        self.player.stop()

    def failed(self, e):
        self.log_line(f"Error: {e}")
        self.done(f"[red]Failed: {e}")
        self.notify(str(e), severity="error", timeout=10)


def run(folder=None):
    DJMix(os.path.abspath(os.path.expanduser(folder)) if folder else None).run()
