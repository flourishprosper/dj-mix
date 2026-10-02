"""Format support: what's mixable, what gets converted, and clean titles."""
import os, subprocess

from djmix.convert import ORIGINALS, convert_folder, convertible
from djmix.util import display_title, list_tracks, song_key


def test_titles_drop_download_ids_and_takes_group():
    f = "BBQ Breakdown - 7a1371ec-0e85-454c-97b9-8ede862d6b73.m4a"
    assert display_title(f) == "BBQ Breakdown"
    assert song_key(display_title(f)) == song_key("BBQ Breakdown (1)") == "bbq breakdown"
    assert display_title("Mix - Part 2.mp3") == "Mix - Part 2"          # ordinary dashes are kept


def test_only_compatible_files_are_tracks_and_m4a_converts(tmp_path):
    d = str(tmp_path)
    for name, fmt in (("a.mp3", "mp3"), ("b - 7a1371ec-0e85-454c-97b9-8ede862d6b73.m4a", "ipod")):
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=220:d=2", "-f", fmt,
                        os.path.join(d, name)], check=True)
    assert list_tracks(d) == ["a.mp3"]
    assert convertible(d) == ["b - 7a1371ec-0e85-454c-97b9-8ede862d6b73.m4a"]
    res = convert_folder(d, log=lambda m: None)
    assert list(res.values()) == ["ok"]
    assert sorted(list_tracks(d)) == ["a.mp3", "b - 7a1371ec-0e85-454c-97b9-8ede862d6b73.mp3"]
    assert convertible(d) == []                                          # original moved aside, not deleted
    assert os.listdir(os.path.join(d, ORIGINALS)) == ["b - 7a1371ec-0e85-454c-97b9-8ede862d6b73.m4a"]
