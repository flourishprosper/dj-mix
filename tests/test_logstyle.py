"""Log colors: content in square brackets survives, and key parts get colored."""
from rich.text import Text

from djmix.logstyle import GOLD, GREEN, LogHighlighter


def styles_at(text, sub):
    i = text.plain.index(sub)
    return " ".join(str(s.style) for s in text.spans if s.start <= i < s.end)


def test_brackets_are_content_and_parts_are_colored():
    h = LogHighlighter()
    h.set_names(["Nod Along 1957.mp3", "Dusty 45 Dreams.mp3"])
    line = "  [6/16]  5. Dusty 45 Dreams.mp3 -> Nod Along 1957.mp3: beatmatched 8 bars, stretch +2.9% [head nod park]"
    t = Text(line)
    h.highlight(t)
    assert t.plain == line                                   # nothing swallowed
    assert GOLD in styles_at(t, "Nod Along 1957.mp3")
    assert GREEN in styles_at(t, "beatmatched")
    assert "italic" in styles_at(t, "[head nod park]")
