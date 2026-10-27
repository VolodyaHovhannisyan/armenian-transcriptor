"""R benchmark for ASR: measure compute-seconds per audio-second on this CPU and
derive the safe max-utterance length for the 5 s budget. Also prints the Armenian
transcription so we can sanity-check quality.

Usage: python bench_asr.py samples/hy_test.wav
"""

import sys
import time

import audio as audio_io
import asr

# Budget components (seconds), per PLAN "Latency design":
SILENCE_ENDPOINT = 0.5
MT_EST = 0.5
EMBED_EST = 0.2
SAFETY = 0.7
BUDGET = 5.0


def max_utterance_len(R: float) -> float:
    return (BUDGET - SILENCE_ENDPOINT - MT_EST - EMBED_EST - SAFETY) / R


def main(wav: str) -> int:
    data, sr = audio_io.read_wav(wav)
    dur = len(data) / sr
    print(f"clip: {wav}  duration={dur:.2f}s  sr={sr}\n")

    for threads in (4, 2):
        print(f"=== cpu_threads={threads} ===")
        t_load = time.monotonic()
        model = asr.ArmenianASR(cpu_threads=threads)
        model.warmup()  # exclude cold-start from timing
        print(f"  load+warmup: {time.monotonic() - t_load:.1f}s")

        # Time a few runs for stability.
        times = []
        text = ""
        for _ in range(3):
            tr = model.transcribe(data)
            times.append(tr.asr_ms / 1000.0)
            text = tr.text
        best = min(times)
        R = best / dur
        print(f"  asr compute: best={best:.2f}s  (R={R:.2f} compute-s/audio-s)")
        print(f"  => max utterance to stay <5s: {max_utterance_len(R):.1f}s")
        print(f"  transcription:\n    {text}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "samples/hy_test.wav"))
