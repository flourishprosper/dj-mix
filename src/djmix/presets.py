"""Genre presets and the settings a mix is rendered with."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Preset:
    name: str
    description: str
    bpm_center: float          # tempo the detector leans toward (breaks half/double-time ties)
    bpm_spread: float          # how hard it leans, in octaves (smaller = stricter)
    bars: int = 8              # default transition length
    max_stretch: float = 0.08  # biggest tempo change allowed when beatmatching
    cut_breakdowns: bool = False
    bpm_min: float = 50
    bpm_max: float = 200


PRESETS = {p.name: p for p in [
    Preset("lofi", "Lo-fi / chillhop, incl. Suno output (cuts choppy endings)",
           88, 0.35, bars=8, cut_breakdowns=True),
    Preset("hiphop", "Hip-hop, boom bap, R&B", 92, 0.35, bars=8),
    Preset("house", "House, disco, deep house", 124, 0.2, bars=16, max_stretch=0.06),
    Preset("techno", "Techno, tech house", 130, 0.2, bars=16, max_stretch=0.06),
    Preset("dnb", "Drum & bass, jungle", 174, 0.2, bars=16, max_stretch=0.06),
    Preset("pop", "Pop / vocal tracks (short transitions)", 110, 0.5, bars=4),
    Preset("auto", "Unknown genre: wide tempo search", 110, 1.0, bars=8),
]}
DEFAULT_PRESET = "lofi"


@dataclass
class Settings:
    preset: Preset = field(default_factory=lambda: PRESETS[DEFAULT_PRESET])
    bars: int | None = None            # None = preset default
    bpm_center: float | None = None    # None = preset default
    cut_breakdowns: bool | None = None # None = preset default
    lufs: float = -14.0
    max_drift: float = 0.030           # s; how far beats may stray from the fitted grid
    seed: int | None = None

    def __post_init__(self):
        p = self.preset
        if self.bars is None:
            self.bars = p.bars
        if self.bpm_center is None:
            self.bpm_center = p.bpm_center
        if self.cut_breakdowns is None:
            self.cut_breakdowns = p.cut_breakdowns

    @property
    def max_stretch(self):
        return self.preset.max_stretch

    @property
    def tempo_prior(self):
        """What the analysis depends on (part of the analysis cache key)."""
        p = self.preset
        return (float(self.bpm_center), p.bpm_spread, p.bpm_min, p.bpm_max)
