"""ASR + MT end-to-end on a clip: print HY + EN and the latency breakdown.

Usage: python bench_translate.py [samples/hy_test.wav] [small|turbo] [600M|1.3B]
"""

import sys
import time

import audio as audio_io
import asr
import translate


def main(wav: str, asr_model: str = "small", mt_model: str = "600M") -> int:
    data, sr = audio_io.read_wav(wav)
    dur = len(data) / sr
    print(f"clip: {wav}  duration={dur:.2f}s  asr={asr_model}  mt={mt_model}\n")

    t = time.monotonic()
    a = asr.ArmenianASR(asr_model)
    m = translate.Translator(mt_model)
    a.warmup(); m.warmup()
    print(f"load+warmup: {time.monotonic() - t:.1f}s\n")

    tr = a.transcribe(data)
    tl = m.translate(tr.text)
    print(f"HY: {tr.text}")
    print(f"EN: {tl.text}\n")
    print(f"asr={tr.asr_ms:.0f}ms  mt={tl.mt_ms:.0f}ms  "
          f"compute_total={(tr.asr_ms + tl.mt_ms)/1000:.2f}s "
          f"(+~0.5s endpoint +~0.2s embed => budget headroom to 5s)")
    return 0


if __name__ == "__main__":
    wav = sys.argv[1] if len(sys.argv) > 1 else "samples/hy_test.wav"
    asr_model = sys.argv[2] if len(sys.argv) > 2 else "small"
    mt_model = sys.argv[3] if len(sys.argv) > 3 else "600M"
    raise SystemExit(main(wav, asr_model, mt_model))
