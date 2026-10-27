"""Voice-activity detection and utterance segmentation (build step 2).

Consumes the tagged 512-sample audio blocks produced by `audio.CaptureStream`
and turns them into *utterances*: contiguous speech runs closed on a trailing
silence. The trailing silence is what defines "end of a spoken phrase" for the
5-second latency clock, so endpointing is the key knob here.

Design notes:
- silero-vad v5 scores exactly 512-sample frames at 16 kHz; our capture block
  size is 512, so one block == one VAD frame (~32 ms).
- A short pre-roll is prepended so the onset of speech isn't clipped.
- Over-long speech is soft-cut at the most recent dip below threshold, so a long
  monologue is emitted in budget-sized pieces instead of blowing the latency
  budget (see PLAN "Latency design" #2).
- The probability source is injectable (`prob_fn`) so the state machine can be
  tested with scripted probabilities, independent of the model.
"""

from __future__ import annotations

import time
import warnings
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

import config

FRAME_SAMPLES = config.BLOCK_SIZE          # 512
FRAME_MS = 1000.0 * FRAME_SAMPLES / config.SAMPLE_RATE  # ~32 ms


@dataclass
class Utterance:
    """A segmented speech run ready for ASR."""

    source_tag: str                 # "me" or "system"
    audio: np.ndarray               # 1-D float32, 16 kHz
    t_end: float                    # monotonic time when endpoint was detected
    duration: float                 # seconds of audio
    soft_cut: bool = False          # True if closed by the max-length cap, not silence


def load_vad_prob_fn() -> Callable[[np.ndarray], float]:
    """Load silero-vad and return a function mapping a 512-sample frame -> speech prob."""
    import torch
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # silero uses deprecated torch.jit.load
        from silero_vad import load_silero_vad
        model = load_silero_vad()

    def prob(frame: np.ndarray) -> float:
        with torch.no_grad():
            return float(model(torch.from_numpy(frame), config.SAMPLE_RATE).item())

    return prob


class StreamingVAD:
    """Per-source streaming segmenter. Feed 512-sample frames; get Utterances back.

    Thresholds are in milliseconds and converted to frame counts internally.
    """

    def __init__(
        self,
        source_tag: str,
        prob_fn: Callable[[np.ndarray], float],
        *,
        threshold: float = 0.5,
        min_silence_ms: int = 500,     # trailing silence that closes an utterance
        min_speech_ms: int = 200,      # ignore blips shorter than this
        speech_pad_ms: int = 150,      # pre/post-roll kept around speech
        max_utterance_s: float = 8.0,  # soft-cut cap (tuned in step 3 via R)
    ):
        self.source_tag = source_tag
        self.prob_fn = prob_fn
        self.threshold = threshold
        self.min_silence_frames = max(1, round(min_silence_ms / FRAME_MS))
        self.min_speech_frames = max(1, round(min_speech_ms / FRAME_MS))
        self.pad_frames = max(0, round(speech_pad_ms / FRAME_MS))
        self.max_frames = max(1, round(max_utterance_s * 1000 / FRAME_MS))

        self._preroll: list[np.ndarray] = []   # recent frames before speech
        self._buf: list[np.ndarray] = []        # frames in the current utterance
        self._in_speech = False
        self._silence_run = 0                    # consecutive sub-threshold frames
        self._speech_frames = 0                  # speech frames seen in this utterance

    def _emit(self, *, soft_cut: bool) -> Utterance | None:
        """Finalize the current utterance buffer into an Utterance (or None if too short)."""
        frames = self._buf
        speech = self._speech_frames
        self._reset_utterance()
        if speech < self.min_speech_frames or not frames:
            return None
        audio = np.concatenate(frames).astype("float32")
        return Utterance(
            source_tag=self.source_tag,
            audio=audio,
            t_end=time.monotonic(),
            duration=len(audio) / config.SAMPLE_RATE,
            soft_cut=soft_cut,
        )

    def _reset_utterance(self) -> None:
        self._buf = []
        self._in_speech = False
        self._silence_run = 0
        self._speech_frames = 0

    def feed(self, frame: np.ndarray) -> Utterance | None:
        """Process one 512-sample frame. Returns an Utterance when one closes, else None."""
        p = self.prob_fn(frame)
        is_speech = p >= self.threshold

        if not self._in_speech:
            # Maintain a rolling pre-roll of recent silence frames.
            self._preroll.append(frame)
            if len(self._preroll) > self.pad_frames:
                self._preroll.pop(0)
            if is_speech:
                self._in_speech = True
                self._buf = list(self._preroll) + [frame]
                self._preroll = []
                self._silence_run = 0
                self._speech_frames = 1
            return None

        # In speech: accumulate.
        self._buf.append(frame)
        if is_speech:
            self._speech_frames += 1
            self._silence_run = 0
        else:
            self._silence_run += 1

        # Close on trailing silence (endpoint).
        if self._silence_run >= self.min_silence_frames:
            return self._emit(soft_cut=False)

        # Soft-cut if the utterance has grown past the cap.
        if len(self._buf) >= self.max_frames:
            return self._emit(soft_cut=True)

        return None
