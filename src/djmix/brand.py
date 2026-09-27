"""Branding: title cards (song + credits) and a persistent watermark,
drawn with Pillow as transparent PNGs (stock Homebrew ffmpeg has no text
filters) and composited by ffmpeg's overlay filter."""
import os, re
from dataclasses import dataclass, field

from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont

AVENIR = "/System/Library/Fonts/Avenir Next.ttc"   # faces: 2 Demi Bold, 5 Medium, 7 Regular
POSITIONS = ("top-left", "top-right", "bottom-left", "bottom-right", "center")

# title card timing (seconds)
FADE_IN, HOLD, FADE_OUT = 1.0, 15.0, 1.5
FIRST_CARD_DELAY = 1.0


@dataclass
class Brand:
    credits: list[str] = field(default_factory=list)   # lines under each song title
    accent: str = "#F2A65A"          # bar beside the title card
    text_color: str = "#F6ECDC"
    font: str | None = None          # .ttf/.otf path; default Avenir Next (macOS)
    watermark_image: str | None = None
    watermark_text: str | None = None
    watermark_pos: str = "top-right"
    watermark_size: float = 0.12     # watermark width as a fraction of frame width
    watermark_opacity: float = 0.6
    watermark_margin: float = 0.03   # fraction of frame width

    @property
    def has_watermark(self):
        return bool(self.watermark_image or self.watermark_text)


def _font(brand, size, face):
    """face: 'title' | 'credit' | 'small'."""
    if brand.font:
        return ImageFont.truetype(brand.font, size)
    if os.path.exists(AVENIR):
        return ImageFont.truetype(AVENIR, size, index={"title": 2, "credit": 5, "small": 7}[face])
    return ImageFont.load_default(size)


def _rgba(color, alpha=255):
    return ImageColor.getrgb(color)[:3] + (alpha,)


def make_card(title, brand, w, h, out_png, scale=1.0, top=None):
    """Lower-left 'now playing' card: title, credit lines, accent bar, soft shadow.
    scale > 1 enlarges it (e.g. for phone-sized vertical clips); top places it at
    a given y instead of the bottom of the frame."""
    s = w / 1280 * scale                             # layout designed at 1280x720
    lines = [(title, _font(brand, round(40 * s), "title"), 255)]
    for n, credit in enumerate(brand.credits):
        lines.append((credit, _font(brand, round((20 if n == 0 else 18) * s), "credit" if n == 0 else "small"),
                      235 if n == 0 else 190))
    gaps = [round(10 * s)] + [round(4 * s)] * (len(lines) - 1)
    heights = [f.getbbox(t)[3] for t, f, _ in lines]
    block = sum(heights) + sum(gaps[:-1])
    x = round(64 * s)
    y = h - round(30 * s) - block if top is None else top

    text = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dt, ds = ImageDraw.Draw(text), ImageDraw.Draw(shadow)
    yy = y
    for (t, f, alpha), lh, gap in zip(lines, heights, gaps):
        dt.text((x, yy), t, font=f, fill=_rgba(brand.text_color, alpha))
        ds.text((x, yy + round(2 * s)), t, font=f, fill=(0, 0, 0, 200))
        yy += lh + gap
    bar_x, bar_w = x - round(16 * s), max(2, round(3 * s))
    dt.rectangle([bar_x, y + round(6 * s), bar_x + bar_w, y + block], fill=_rgba(brand.accent))
    ds.rectangle([bar_x, y + round(8 * s), bar_x + bar_w, y + block + round(2 * s)], fill=(0, 0, 0, 200))

    shadow = shadow.filter(ImageFilter.GaussianBlur(round(5 * s)))
    Image.alpha_composite(shadow, text).save(out_png)


def make_watermark(brand, w, h, out_png):
    """Full-frame transparent PNG with the logo or text in its corner."""
    target_w = max(8, round(brand.watermark_size * w))
    if brand.watermark_image:
        mark = Image.open(brand.watermark_image).convert("RGBA")
        mark = mark.resize((target_w, max(1, round(mark.height * target_w / mark.width))), Image.LANCZOS)
    else:
        text = brand.watermark_text
        size = 100
        f = _font(brand, size, "title")
        tw = f.getbbox(text)[2]
        f = _font(brand, max(6, round(size * target_w / max(tw, 1))), "title")
        l, t, r, b = f.getbbox(text)
        pad = max(2, round(w / 640))
        mark = Image.new("RGBA", (r + 2 * pad, b + 2 * pad), (0, 0, 0, 0))
        sh = Image.new("RGBA", mark.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).text((pad, pad + 1), text, font=f, fill=(0, 0, 0, 180))
        ImageDraw.Draw(mark).text((pad, pad), text, font=f, fill=_rgba(brand.text_color))
        mark = Image.alpha_composite(sh.filter(ImageFilter.GaussianBlur(pad)), mark)
    alpha = mark.getchannel("A").point(lambda v: round(v * brand.watermark_opacity))
    mark.putalpha(alpha)

    m = round(brand.watermark_margin * w)
    pos = brand.watermark_pos
    x = {"left": m, "right": w - mark.width - m}.get(pos.split("-")[-1], (w - mark.width) // 2)
    y = m if pos.startswith("top") else h - mark.height - m if pos.startswith("bottom") \
        else (h - mark.height) // 2
    frame = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    frame.alpha_composite(mark, (x, y))
    frame.save(out_png)


def parse_tracklist(path):
    cards = []
    for line in open(path):
        m = re.match(r"^(\d+(?::\d+){1,2})\s+(.+)$", line.strip())
        if m:
            parts = [int(p) for p in m.group(1).split(":")]
            cards.append((sum(p * 60 ** i for i, p in enumerate(reversed(parts))), m.group(2)))
    return cards
