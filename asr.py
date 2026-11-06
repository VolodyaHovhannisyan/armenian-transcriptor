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

# Named model presets -> CT2 directory. `small` is the latency-safe default;
# `turbo` is the up-tier (whisper-large-v3-turbo-armenian, ~15% WER vs ~24%),
# which is viable here because turbo has only 4 decoder layers. Convert both with
# setup_models.sh and benchmark R with bench_asr.py before making turbo default.
MODELS = {
    "small": "models/whisper-small-hy-ct2",
    "turbo": "models/whisper-turbo-hy-ct2",
}
DEFAULT_MODEL = "small"
DEFAULT_MODEL_DIR = MODELS[DEFAULT_MODEL]

# Temperature fallback schedule: greedy first, then retry hotter only when the
# decode looks bad (compression/log-prob thresholds below). Costs nothing on good
# utterances; rescues the occasional garbage one instead of printing it.
DEFAULT_TEMPERATURES = (0.0, 0.2, 0.4, 0.6)


def resolve_model_path(model: str) -> str:
    """Accept a preset name (small/turbo) or an explicit path."""
    return MODELS.get(model, model)


@dataclass
class Transcript:
    text: str          # Armenian text (may be empty for non-speech)
    asr_ms: float      # wall-clock compute time for this utterance


class ArmenianASR:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        compute_type: str = "int8",
        cpu_threads: int = 2,  # benchmarked faster than 4 on this 2-core CPU (hyperthreads hurt)
        language: str = "hy",
        beam_size: int = 5,    # was greedy (1); beam=5 cuts WER with budget headroom
        temperatures=DEFAULT_TEMPERATURES,
        initial_prompt: str | None = None,
    ):
        from faster_whisper import WhisperModel

        self.language = language
        self.beam_size = beam_size
        self.temperatures = list(temperatures)
        self.initial_prompt = initial_prompt
        self.model = WhisperModel(
            resolve_model_path(model),
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
            beam_size=self.beam_size,
            temperature=self.temperatures,
            # Fallback triggers: retry hotter when a greedy decode looks degenerate.
            compression_ratio_threshold=2.4,
            log_prob_threshold=-1.0,
            condition_on_previous_text=False,
            initial_prompt=self.initial_prompt,
            vad_filter=False,
            without_timestamps=True,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return Transcript(text=text, asr_ms=(time.monotonic() - t0) * 1000.0)

    def warmup(self) -> None:
        """Run a dummy inference so the first real utterance isn't penalised."""
        self.transcribe(np.zeros(config.SAMPLE_RATE, dtype="float32"))
