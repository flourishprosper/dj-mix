"""Per-folder memory: <folder>/_dj-mix/

  history.json   every render (settings, seed, outputs, timeline of where each
                 song sits in the mix) and every promo clip exported from it,
                 plus the last settings used in this folder
  log.txt        human-readable diary of everything done here
  analysis.json  per-track analysis cache

So opening a folder picks up where you left off, and any past render can
still be cut into promo clips.
"""
import json, os, re, time, uuid

DIRNAME = "_dj-mix"


def path(folder, name=None):
    d = os.path.join(folder, DIRNAME)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name) if name else d


def load(folder):
    try:
        with open(path(folder, "history.json")) as f:
            h = json.load(f)
    except (OSError, ValueError):
        h = {}
    h.setdefault("renders", [])
    h.setdefault("last", {})
    return h


def save(folder, h):
    tmp = path(folder, "history.json.tmp")
    with open(tmp, "w") as f:
        json.dump(h, f, indent=1)
    os.replace(tmp, path(folder, "history.json"))


def log(folder, msg):
    with open(path(folder, "log.txt"), "a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")


def remember_settings(folder, **last):
    """Settings to restore next time this folder is opened."""
    h = load(folder)
    h["last"].update({k: v for k, v in last.items()})
    save(folder, h)


def add_render(folder, record):
    h = load(folder)
    record.setdefault("id", uuid.uuid4().hex[:8])
    record.setdefault("created", time.strftime("%Y-%m-%d %H:%M"))
    record.setdefault("promos", [])
    h["renders"].append(record)
    save(folder, h)
    return record


def update_render(folder, render_id, **fields):
    h = load(folder)
    for r in h["renders"]:
        if r["id"] == render_id:
            r.update(fields)
    save(folder, h)


def add_promo(folder, render_id, promo):
    h = load(folder)
    for r in h["renders"]:
        if r["id"] == render_id:
            promo.setdefault("created", time.strftime("%Y-%m-%d %H:%M"))
            r.setdefault("promos", []).append(promo)
    save(folder, h)


def renders(folder):
    """Renders newest first, each with `exists` telling whether its output is still there."""
    out = []
    for r in sorted(load(folder)["renders"], key=lambda r: r.get("created", ""), reverse=True):
        f = r.get("video") or r.get("audio")
        out.append(dict(r, exists=bool(f) and os.path.exists(os.path.join(folder, f))))
    return out


def parse_tracklist(path_):
    """Timestamps + the settings footer, e.g. '(seed 287710, preset lofi, 8 bars)'."""
    stamps, seed, preset, bars = [], None, None, None
    for line in open(path_):
        line = line.strip()
        m = re.match(r"^(\d+(?::\d+){1,2})\s+(.+)$", line)
        if m:
            parts = [int(p) for p in m.group(1).split(":")]
            stamps.append((sum(p * 60 ** i for i, p in enumerate(reversed(parts))), m.group(2)))
            continue
        m = re.search(r"seed (\d+)", line)
        if m:
            seed = int(m.group(1))
            pm = re.search(r"preset (\w+)", line)
            bm = re.search(r"(\d+) bars", line)
            preset = pm.group(1) if pm else None
            bars = int(bm.group(1)) if bm else None
    return stamps, seed, preset, bars
