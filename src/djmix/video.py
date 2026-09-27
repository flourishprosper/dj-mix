"""Video encode: loop the clip under the mix, with optional title cards and
watermark, in a single pass."""
import math, os, shutil, subprocess, tempfile

from . import brand as brandmod
from .util import SR, probe


def encode(video, audio, out, duration, cards=(), brand=None, copy_audio=False, progress=None):
    """video: loop clip. audio: mix WAV (limited + AAC encoded) or an existing
    MP4 whose audio is copied (copy_audio=True). cards: [(time_s, title)]."""
    w, h = map(int, probe(video, "stream=width,height", "v:0").split(",")[:2])
    vdur = float(probe(video, "format=duration"))
    inputs = ["-stream_loop", str(math.ceil(duration / vdur)), "-i", video, "-i", audio]
    chains, last, k = [], "[0:v]", 2
    work = tempfile.mkdtemp()
    try:
        if brand and brand.has_watermark:
            wm = os.path.join(work, "watermark.png")
            brandmod.make_watermark(brand, w, h, wm)
            inputs += ["-loop", "1", "-framerate", "24", "-i", wm]
            chains.append(f"{last}[{k}:v]overlay=shortest=0:format=auto[wm]")
            last, k = "[wm]", k + 1
        card_len = brandmod.FADE_IN + brandmod.HOLD + brandmod.FADE_OUT
        for i, (t, title) in enumerate(cards):
            png = os.path.join(work, f"card{i:02d}.png")
            brandmod.make_card(title, brand or brandmod.Brand(), w, h, png)
            inputs += ["-loop", "1", "-framerate", "24", "-t", f"{card_len}", "-i", png]
            start = max(t, brandmod.FIRST_CARD_DELAY)
            chains.append(
                f"[{k}:v]format=rgba,"
                f"fade=t=in:st=0:d={brandmod.FADE_IN}:alpha=1,"
                f"fade=t=out:st={brandmod.FADE_IN + brandmod.HOLD}:d={brandmod.FADE_OUT}:alpha=1,"
                f"setpts=PTS-STARTPTS+{start}/TB[c{i}]")
            chains.append(f"{last}[c{i}]overlay=eof_action=pass[v{i}]")
            last, k = f"[v{i}]", k + 1
        chains.append(f"{last}format=yuv420p[vout]")

        audio_args = (["-c:a", "copy"] if copy_audio else
                      ["-af", "alimiter=limit=0.89:level=false", "-c:a", "aac", "-b:a", "320k", "-ar", str(SR)])
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-nostats", "-progress", "pipe:1", *inputs,
               "-filter_complex", ";".join(chains), "-map", "[vout]", "-map", "1:a:0",
               "-c:v", "libx264", "-crf", "18", "-preset", "medium",
               "-x264-params", "keyint=48:min-keyint=48:scenecut=0",
               *audio_args, "-movflags", "+faststart",
               "-t", f"{duration:.3f}", out]     # -t, not -shortest: -shortest overshoots ~3 s
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for line in proc.stdout:
            if progress and line.startswith("out_time_us="):
                try:
                    progress(min(1.0, int(line.split("=")[1]) / 1e6 / duration))
                except ValueError:
                    pass
        err = proc.stderr.read()
        if proc.wait():
            raise RuntimeError(f"ffmpeg failed:\n{err}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
