"""Colors for the Log tab: each kind of information gets its own color so a
line is easy to scan (counters, track names, BPM, keys, times, results...).

Log lines are plain text (square brackets like "[head nod park]" are content,
not markup); this highlighter adds the colors.
"""
import re

from rich.highlighter import Highlighter
from rich.text import Text

BLUE, GOLD, PURPLE, CYAN, TEAL = "#7aa2f7", "#e0af68", "#bb9af7", "#7dcfff", "#73daca"
ORANGE, GREEN, RED, MUTED, DIM = "#ff9e64", "#9ece6a", "#f7768e", "#a9b1d6", "#565f89"

# (pattern, style) in order: later rules win where they overlap
RULES = [
    (r"^\s*(Analyzing|Rendering audio|Encoding video|Exporting|Converting|Planning|Refreshing|Cutting)\b.*", f"bold {BLUE}"),
    (r"\b\d{1,2}:\d{2}(:\d{2})?\b", TEAL),                                  # times / durations
    (r"(?<![\w.])\d+(\.\d+)?s\b", TEAL),                                    # 30s, 8s
    (r"\b\d+[- ]bars?\b", TEAL),                                            # 8 bars / 8-bar
    (r"\[\d+/\d+\]", f"bold {BLUE}"),                                       # [3/16]
    (r"(?<=\s)\d{1,3}\.(?=\s)", BLUE),                                      # " 12. "
    (r"\d+(\.\d+)?\s?BPM\b", PURPLE),
    (r"-?\d+(\.\d+)?\s?LUFS\b", PURPLE),
    (r"\bseed \d+", PURPLE),
    (r"(?<![\w#])(1[0-2]|[1-9])[AB]\b", f"bold {CYAN}"),                    # Camelot key
    (r"\[[^\]\d/][^\]]*\]", f"italic {MUTED}"),                             # [song group]
    (r"[+-]\d+(\.\d+)?%", ORANGE),                                          # stretch
    (r"\b\d+ ms\b", ORANGE),                                                # beat offset
    (r"\bplain \d+s crossfade\b.*|\bcrossfade\b", f"italic {MUTED}"),
    (r"\bbeatmatch(ed)?\b", f"bold {GREEN}"),
    (r"\b\d+/\d+(?= transitions)", f"bold {GREEN}"),
    (r"\b(Done|Rendered in|Converted|Exported|ok)\b", f"bold {GREEN}"),
    (r"\bMIX_[\w.-]+", f"bold {GOLD}"),
    (r"->|→", DIM),
    (r"\bskipped\b.*", ORANGE),
    (r"⚠.*", f"bold {ORANGE}"),
    (r"\b(Error|Failed|failed)\b.*", f"bold {RED}"),
    (r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", DIM),                         # diary timestamps
]
COMPILED = [(re.compile(p), s) for p, s in RULES]


class LogHighlighter(Highlighter):
    def __init__(self):
        super().__init__()
        self.names = []                    # track file names / titles in the open folder

    def set_names(self, names):
        # longest first, so "Song (1).mp3" wins over "Song"
        self.names = sorted({n for n in names if len(n) > 2}, key=len, reverse=True)

    def highlight(self, text: Text):
        plain = text.plain
        for rx, style in COMPILED[:-6]:
            for m in rx.finditer(plain):
                text.stylize(style, m.start(), m.end())
        for name in self.names:            # exact track names (they contain spaces)
            start = 0
            while (i := plain.find(name, start)) != -1:
                text.stylize(f"bold {GOLD}", i, i + len(name))
                start = i + len(name)
        for rx, style in COMPILED[-6:]:    # arrows, warnings, errors, timestamps on top
            for m in rx.finditer(plain):
                text.stylize(style, m.start(), m.end())
