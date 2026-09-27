"""Promo clip placement, song timelines, and per-folder history."""
import numpy as np
import pytest

from djmix import history, promo


def song(full_in=100.0, out_start=200.0, bpm=86.0):
    beat = 60 / bpm
    return dict(title="Song", start=full_in - 10, full_in=full_in, out_start=out_start,
                end=out_start + 10, chapter=full_in - 5,
                downbeats=[round(full_in - 10 + k * 4 * beat, 3) for k in range(60)])


@pytest.fixture
def flat_energy(monkeypatch):
    """No audio needed: every moment is equally loud unless a test says otherwise."""
    monkeypatch.setattr(promo, "_energy", lambda video, lo, hi, **kw: (np.ones(int((hi - lo) / 0.25) + 1), 0.25))


def test_clip_stays_in_solo_section_on_a_downbeat_after_the_title_card(flat_energy):
    s = song()
    start, length, clean = promo.choose_clip("mix.mp4", s, 30, titles_in_mix=True)
    assert clean and length == 30
    assert s["full_in"] <= start and start + length <= s["out_start"]
    assert start in s["downbeats"]
    assert start >= s["chapter"] + promo.CARD_WINDOW          # mix's own card has gone


def test_clip_goes_to_the_loudest_part(monkeypatch):
    s = song(full_in=100, out_start=300)
    def energy(video, lo, hi, **kw):
        t = lo + np.arange(int((hi - lo) / 0.25) + 1) * 0.25
        return np.where((t > 230) & (t < 262), 5.0, 1.0), 0.25   # a loud chorus at 230-262 s
    monkeypatch.setattr(promo, "_energy", energy)
    start, _, _ = promo.choose_clip("mix.mp4", s, 30)
    assert 225 <= start <= 235


def test_short_solo_section_falls_back_and_says_so(flat_energy):
    s = song(full_in=100, out_start=115)                      # only 15 s alone
    start, length, clean = promo.choose_clip("mix.mp4", s, 30)
    assert not clean
    assert s["start"] <= start and start + length <= s["end"]


def test_timeline_maps_beats_into_mix_time():
    beats = list(np.arange(0, 120, 0.5))
    track = dict(file="a.mp3", song="a", beats=beats, kick=[1.0 if i % 4 == 0 else 0.1 for i in range(len(beats))])
    seg = dict(start=50.0, full_in=60.0, out_start=150.0, end=160.0, chapter=55.0, shift=50.0, ratio=1.0)
    tl = promo.build_timeline([track], [seg])[0]
    assert tl["downbeats"] and all(50 <= d <= 160 for d in tl["downbeats"])
    assert all(abs(((d - 50) / 2.0) - round((d - 50) / 2.0)) < 1e-6 for d in tl["downbeats"])  # every bar (4 x 0.5 s)


def test_tracklist_footer_is_parsed(tmp_path):
    p = tmp_path / "MIX_x_tracklist.txt"
    p.write_text("0:00 One\n2:13 Two\n1:02:03 Three\n\n(seed 287710, preset house, 16 bars)\n"
                 "(rendered in 4:52 on 2026-09-26 20:14)\n")
    stamps, seed, preset, bars = history.parse_tracklist(p)
    assert stamps == [(0, "One"), (133, "Two"), (3723, "Three")]
    assert (seed, preset, bars) == (287710, "house", 16)


def test_history_remembers_renders_promos_and_last_settings(tmp_path):
    f = str(tmp_path)
    r = history.add_render(f, dict(video="MIX_a.mp4", seed=1, created="2026-01-01 10:00"))
    history.add_render(f, dict(video="MIX_b.mp4", seed=2, created="2026-01-02 10:00"))
    history.add_promo(f, r["id"], dict(song=0, title="One", file="MIX_a_promo/01 One.mp4", format="vertical"))
    history.remember_settings(f, seed=2, preset="lofi", promo_fmt="square")
    rs = history.renders(f)
    assert [x["video"] for x in rs] == ["MIX_b.mp4", "MIX_a.mp4"]               # newest first
    assert len(rs[1]["promos"]) == 1 and not rs[0]["exists"]
    assert history.load(f)["last"] == dict(seed=2, preset="lofi", promo_fmt="square")
