"""A small audio player for the interface: any file ffmpeg can read (tracks,
mix videos), with pause, seek, volume and an optional stop point (clip preview).

ffmpeg decodes from the requested position into a bounded buffer on a reader
thread; sounddevice pulls from that buffer in its audio callback. Seeking just
restarts ffmpeg at the new position, so long mixes start instantly.
"""
import subprocess, threading, time
from collections import deque

import numpy as np

RATE = 48000
CHANNELS = 2
BLOCK = 2048                     # frames per ffmpeg read
MAX_BUFFER = 48                  # blocks queued ahead (~2 s)


class Player:
    def __init__(self):
        self.title = ""
        self.path = None
        self.duration = 0.0
        self.volume = 0.8
        self.paused = False
        self.finished = False
        self._stream = None
        self._proc = None
        self._reader = None
        self._buf = deque()
        self._lock = threading.Lock()
        self._space = threading.Condition(self._lock)
        self._start = 0.0            # file position (s) the current decode started at
        self._frames = 0             # frames played since then
        self._end = None             # stop at this file position (s), for clip previews
        self._clip_start = 0.0       # where play() started (a clip's start); replay/seek floor
        self._eof = False
        self._gen = 0                # bumps on every (re)start so old threads quit

    # ---------------------------------------------------------------- control
    def play(self, path, title="", start=0.0, end=None):
        """Play `path` from `start` seconds, stopping at `end` if given."""
        from .util import probe
        self.stop()
        self.path, self.title = path, title
        try:
            self.duration = float(probe(path, "format=duration"))
        except Exception:
            self.duration = 0.0
        self._end = end
        self._clip_start = start
        self._begin(start)

    def toggle(self):
        if not self._stream:
            if self.path and self.finished:            # replay from the top
                self._begin(self._clip_start)
            return
        self.paused = not self.paused
        (self._stream.stop if self.paused else self._stream.start)()

    def seek(self, delta):
        if self.path:
            lo = self._clip_start if self._end is not None else 0.0
            hi = (self._end if self._end is not None else self.duration) - 0.5
            was_paused = self.paused
            self._begin(min(max(lo, self.position + delta), max(lo, hi)))
            if was_paused:
                self.toggle()

    def set_volume(self, v):
        self.volume = min(1.0, max(0.0, v))

    def stop(self):
        self._gen += 1
        if self._stream:
            try:
                self._stream.stop(); self._stream.close()
            except Exception:
                pass
        self._stream = None
        if self._proc:
            self._proc.kill()
            self._proc = None
        with self._lock:
            self._buf.clear()
            self._space.notify_all()
        self.paused = False

    # ---------------------------------------------------------------- state
    @property
    def playing(self):
        return self._stream is not None and not self.paused

    @property
    def position(self):
        return self._start + self._frames / RATE

    @property
    def length(self):
        """Where playback ends: the clip end, or the file's duration."""
        return self._end if self._end is not None else self.duration

    # ---------------------------------------------------------------- internals
    def _begin(self, start):
        import sounddevice as sd
        self.stop()
        gen = self._gen
        self._start, self._frames, self._eof, self.finished = start, 0, False, False
        dur = ["-t", f"{self._end - start:.3f}"] if self._end is not None else []
        self._proc = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-i", self.path, *dur, "-map", "0:a:0",
             "-ac", str(CHANNELS), "-ar", str(RATE), "-f", "f32le", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self._reader = threading.Thread(target=self._read, args=(self._proc, gen), daemon=True)
        self._reader.start()
        self._stream = sd.OutputStream(samplerate=RATE, channels=CHANNELS, dtype="float32",
                                       blocksize=1024, callback=self._callback)
        self._stream.start()

    def _read(self, proc, gen):
        nbytes = BLOCK * CHANNELS * 4
        while gen == self._gen:
            data = proc.stdout.read(nbytes)
            if not data:
                break
            block = np.frombuffer(data, dtype=np.float32).reshape(-1, CHANNELS)
            with self._space:
                while len(self._buf) >= MAX_BUFFER and gen == self._gen:
                    self._space.wait(0.2)
                if gen != self._gen:
                    return
                self._buf.append(block)
        if gen == self._gen:
            self._eof = True

    def _callback(self, out, frames, time_info, status):
        filled = 0
        with self._space:
            while filled < frames and self._buf:
                block = self._buf[0]
                take = min(frames - filled, len(block))
                out[filled:filled + take] = block[:take] * self.volume
                if take < len(block):
                    self._buf[0] = block[take:]
                else:
                    self._buf.popleft()
                filled += take
            self._space.notify_all()
        if filled < frames:
            out[filled:] = 0
            if self._eof and not self._buf:
                self.finished = True
        self._frames += filled
