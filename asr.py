"""Armenian speech recognition via faster-whisper (build step 3).

Wraps a CTranslate2 Whisper model (default: Chillarmo/whisper-small-armenian-v2
converted to int8) and transcribes an utterance's audio to Armenian text.

Decode settings favour latency over peak accuracy, per PLAN "Latency design" #4:
greedy (beam_size=1), no temperature fallback, no previous-text conditioning, no
word timestamps. We already segmented with silero, so Whisper's own VAD is off.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

import config

DEFAULT_MODEL_DIR = "models/whisper-small-hy-ct2"


@dataclass
class Transcript:
    text: str          # Armenian text (may be empty for non-speech)
    asr_ms: float      # wall-clock compute time for this utterance


class ArmenianASR:
    def __init__(
        self,
        model_path: str = DEFAULT_MODEL_DIR,
        *,
        compute_type: str = "int8",
        cpu_threads: int = 2,  # benchmarked faster than 4 on this 2-core CPU (hyperthreads hurt)
        language: str = "hy",
    ):
        from faster_whisper import WhisperModel

        self.language = language
        self.model = WhisperModel(
            model_path,
            device="cpu",
            compute_type=compute_type,
            cpu_threads=cpu_threads,
            num_workers=1,
        )

    def transcribe(self, audio: np.ndarray) -> Transcript:
        """Transcribe 16 kHz mono float32 audio to Armenian text."""
        audio = np.ascontiguousarray(audio, dtype="float32")
        t0 = time.monotonic()
        segments, _info = self.model.transcribe(
            audio,
            language=self.language,
            beam_size=1,
            temperature=0.0,
            condition_on_previous_text=False,
            vad_filter=False,
            without_timestamps=True,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return Transcript(text=text, asr_ms=(time.monotonic() - t0) * 1000.0)

    def warmup(self) -> None:
        """Run a dummy inference so the first real utterance isn't penalised."""
        self.transcribe(np.zeros(config.SAMPLE_RATE, dtype="float32"))
