"""Audio capture: enumerate devices and open mic + system-audio streams.

On Linux with PulseAudio/PipeWire, "system audio" (the far side of a call) is the
*monitor* of the output sink. We capture it alongside the microphone, in a single
process, using sounddevice's PortAudio "pulse" device plus the PULSE_SOURCE
environment variable: PulseAudio binds a recording stream to its source at connect
time, so we set PULSE_SOURCE just before opening each stream and each one locks
onto the source we asked for.

This module is the lowest layer of the pipeline. It knows nothing about VAD, ASR or
translation; it only produces tagged blocks of float32 audio.
"""

from __future__ import annotations

import os
import queue
import subprocess
import threading
from dataclasses import dataclass

import numpy as np

import config

# `sounddevice` is imported lazily inside CaptureStream.start(): it requires the
# system PortAudio library at import time, but device *listing* (via pactl) must
# work without it.


@dataclass
class PulseSource:
    """A PulseAudio capture source (a real input, or a sink's monitor)."""

    name: str          # PulseAudio source name (what we pass to PULSE_SOURCE)
    description: str    # human-readable label
    is_monitor: bool    # True for sink monitors (system audio)


def list_pulse_sources() -> list[PulseSource]:
    """Return available PulseAudio sources via `pactl`.

    Monitors (sink outputs) are flagged so the UI can suggest them for system audio.
    """
    try:
        out = subprocess.run(
            ["pactl", "list", "sources"],
            capture_output=True, text=True, check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []

    sources: list[PulseSource] = []
    name: str | None = None
    desc = ""
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("Name:"):
            name = stripped.split("Name:", 1)[1].strip()
        elif stripped.startswith("Description:"):
            desc = stripped.split("Description:", 1)[1].strip()
            if name is not None:
                sources.append(
                    PulseSource(name=name, description=desc,
                                is_monitor=name.endswith(".monitor"))
                )
                name = None
                desc = ""
    return sources


def default_sink_monitor() -> str | None:
    """Return the monitor source name of the default output sink, if any."""
    try:
        sink = subprocess.run(
            ["pactl", "get-default-sink"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return f"{sink}.monitor" if sink else None


def default_source() -> str | None:
    """Return the default input (microphone) source name, if any."""
    try:
        src = subprocess.run(
            ["pactl", "get-default-source"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return src or None


def resolve_source(selector: str | None, sources: list[PulseSource]) -> str | None:
    """Resolve a user selector (exact name or case-insensitive substring) to a source name."""
    if not selector:
        return None
    for s in sources:  # exact match first
        if s.name == selector:
            return s.name
    needle = selector.lower()
    matches = [s for s in sources if needle in s.name.lower()
               or needle in s.description.lower()]
    if len(matches) == 1:
        return matches[0].name
    if len(matches) > 1:
        names = ", ".join(m.name for m in matches)
        raise ValueError(f"selector {selector!r} is ambiguous: {names}")
    raise ValueError(f"no source matches {selector!r}")


def write_wav(path: str, audio: np.ndarray, sample_rate: int = config.SAMPLE_RATE) -> None:
    """Write mono float32 audio (-1..1) to a 16-bit PCM WAV using the stdlib."""
    import wave

    clipped = np.clip(audio, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())


def read_wav(path: str) -> tuple[np.ndarray, int]:
    """Read a 16-bit PCM mono WAV into float32 (-1..1). (For benchmarking/tests.)"""
    import wave

    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        frames = w.readframes(w.getnframes())
    audio = np.frombuffer(frames, dtype="<i2").astype("float32") / 32767.0
    return audio, sr


def rms(block: np.ndarray) -> float:
    """Root-mean-square level of a float32 audio block (0.0 .. ~1.0)."""
    if block.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(block, dtype=np.float64))))


class CaptureStream:
    """One PulseAudio capture stream, tagged with a source label.

    Audio blocks (1-D float32, mono, 16 kHz) are pushed to `out_queue` as tuples
    `(source_tag, block, frame_index)`. `frame_index` counts frames since start so
    downstream stages can reconstruct timing.
    """

    def __init__(self, pulse_source: str, source_tag: str,
                 out_queue: "queue.Queue"):
        self.pulse_source = pulse_source
        self.source_tag = source_tag
        self.out_queue = out_queue
        self._stream = None  # sd.InputStream, created in start()
        self._frames = 0

    def _callback(self, indata, frames, time_info, status):  # noqa: ANN001
        if status:
            # Overflows etc. are non-fatal; surface them without crashing capture.
            print(f"[audio:{self.source_tag}] {status}", flush=True)
        block = indata[:, 0].copy()  # mono
        self.out_queue.put((self.source_tag, block, self._frames))
        self._frames += frames

    def start(self) -> None:
        import sounddevice as sd  # requires system PortAudio; imported lazily

        # Bind this stream to the requested Pulse source at open time.
        prev = os.environ.get("PULSE_SOURCE")
        os.environ["PULSE_SOURCE"] = self.pulse_source
        try:
            self._stream = sd.InputStream(
                device=config.PULSE_DEVICE,
                samplerate=config.SAMPLE_RATE,
                channels=config.CHANNELS,
                dtype=config.DTYPE,
                blocksize=config.BLOCK_SIZE,
                callback=self._callback,
            )
            self._stream.start()
        finally:
            # Restore env immediately; the stream has already bound its source.
            if prev is None:
                os.environ.pop("PULSE_SOURCE", None)
            else:
                os.environ["PULSE_SOURCE"] = prev

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
