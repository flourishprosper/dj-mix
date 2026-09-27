# dj-mix

Beat-matched, EQ-crossfaded DJ mixes from a folder of tracks — plus a
YouTube-ready video with looping visuals, title cards and a watermark.

It mixes the way a DJ does, not a sequencer:

- **Local beatmatching.** At each transition the incoming track is time-stretched
  (pitch preserved) to the outgoing track's tempo *at that moment*, and the two
  are lined up downbeat to downbeat on phrase boundaries. Handles tracks whose
  tempo drifts (e.g. AI-generated music).
- **EQ transitions.** Highs crossfade across the whole overlap; the bass swaps on
  the middle downbeat so two basslines never stack.
- **Safe fallback.** If the tempo gap is too big or the beats won't stay together,
  that transition becomes a plain equal-power crossfade instead of a trainwreck.
- **Smart order.** Alternate takes of the same song (grouped by the embedded title
  tag, so renaming files doesn't break it) are never back to back; neighbours are
  chosen for close tempo and compatible key (Camelot wheel).
- **Choppy-ending cut.** Detects where a track degrades into flickering "packets"
  of sound (a common Suno artifact) and mixes out before it.
- **Loudness.** Every track matched to -14 LUFS (YouTube's target), limited on output.

## Install

```sh
brew install ffmpeg rubberband uv
git clone <this repo> && cd dj-mix
uv tool install -e .        # puts `dj-mix` on your PATH; -e = code edits apply immediately
```

## Use

```sh
dj-mix                       # open the interface
dj-mix ui ~/Music/my-set     # ...starting in a folder
```

The interface: pick a folder on the left, set options in the middle, then
**Analyze** (`a`) → **Plan** (`p`) to preview the order and every transition →
**Render** (`r`). Render always reproduces the plan you previewed (same seed).
Your options and brand settings are remembered.

Or from the command line:

```sh
dj-mix analyze FOLDER                     # tempo / key / stability / breakdowns per track
dj-mix plan FOLDER                        # order + every transition, no rendering
dj-mix mix FOLDER --titles                # render mix + video with title cards
dj-mix mix FOLDER --seed 287710           # reproduce a previous order exactly
dj-mix titles MIX_....mp4                 # add title cards / watermark to an existing mix video
```

The folder should contain the tracks (mp3, wav, flac, m4a, aif) and, for video,
one looping clip (mp4/mov). Output lands in the same folder as
`MIX_<date>.mp4` plus `MIX_<date>_tracklist.txt` — paste the tracklist into a
YouTube description for chapters.

### Mixing options

| Flag | |
|---|---|
| `--preset` | `lofi` (default), `hiphop`, `house`, `techno`, `dnb`, `pop`, `auto` |
| `--bars N` | transition length in bars (default: preset) |
| `--bpm N` | tempo the detector leans toward, to settle half/double-time (default: preset) |
| `--seed N` | repeat a previous order |
| `--lufs N` | target loudness (default -14) |
| `--keep-endings` / `--cut-endings` | choppy-breakdown cut on/off (on for `lofi`) |
| `--video PATH` | loop clip (default: first video in the folder) |
| `--audio-only` | WAV only, no video |

### Brand / watermark options

Save defaults once with `--save-brand` (stored in `~/.config/dj-mix/config.json`);
the interface edits the same settings.

| Flag | |
|---|---|
| `--titles` | title card at each song: fades in, holds 15 s, fades out |
| `--credit "LINE"` | credit line under each title (repeat for more lines); `--no-credits` |
| `--accent COLOR` / `--text-color COLOR` | title-card colors, e.g. `'#F2A65A'` |
| `--font FILE` | .ttf/.otf for titles and text watermark (default Avenir Next) |
| `--watermark IMAGE` | logo watermark (PNG with transparency) |
| `--watermark-text TEXT` | text watermark |
| `--watermark-pos` | `top-left`, `top-right` (default), `bottom-left`, `bottom-right`, `center` |
| `--watermark-size F` | width as a fraction of the frame (default 0.12) |
| `--watermark-opacity F` | 0–1 (default 0.6) |
| `--watermark-margin F` | distance from the edge, fraction of frame width (default 0.03) |
| `--no-watermark` | ignore the saved watermark for this run |

## What it's good at

Steady-tempo 4/4 music: lo-fi, hip-hop, R&B, house, techno, disco, pop, DnB.
Live-played music (rock, jazz, oldies) mostly gets clean crossfades rather than
beatmatches, because human tempo drifts. Not yet supported: non-4/4 meters,
vocal-aware transition placement.

## Developing

```sh
uv sync
uv run pytest            # synthetic-beat tests: tempo per genre preset, downbeats, breakdowns, ordering
uv run dj-mix ...        # run from the checkout
```

Code map (`src/djmix/`):

| Module | |
|---|---|
| `analyze.py` | tempo, DP beat tracker, kick energy, key, loudness, breakdown detection; per-folder cache |
| `plan.py` | track ordering, downbeat/phrase picking, transition planning, dry run |
| `render.py` | decode, stretch (rubberband), gain, EQ transitions, WAV output |
| `video.py` | loop clip + audio + title cards + watermark in one ffmpeg pass |
| `brand.py` | title-card and watermark drawing (Pillow) |
| `presets.py` | genre presets and render settings — **start here to add a genre** |
| `pipeline.py` | analyze → plan → render → encode, shared by CLI and interface |
| `cli.py`, `tui.py` | command line and Textual interface |

To add a genre: add a `Preset` in `presets.py` and a case to
`tests/test_analysis.py::test_tempo_per_genre`.

Note: librosa's `beat_track` segfaults (numba) on some macOS setups, so beats come
from our own dynamic-programming tracker in `analyze.py`.
