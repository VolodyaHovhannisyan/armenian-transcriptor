# Live Armenian → English Transcriber

Listens to a live conversation, transcribes spoken **Armenian**, and prints an
**English** translation in the terminal within ~5 seconds, labelled by speaker —
like a video call, but in the terminal.

It captures two audio sources at once: your **microphone** (labelled `Me`) and the
**system audio output** (the far side of a call), telling the far-side speakers
apart as `Speaker 1`, `Speaker 2`, … Everything runs **locally** — no cloud speech
or translation APIs.

```
[00:00:03] Speaker 1  HY: Ողջույն, ես հիմա խոսում եմ հայերեն։   EN: Hello, I am speaking Armenian now.
[00:00:07] Me         HY: Լավ եմ, շնորհակալություն։             EN: I'm fine, thank you.
```

---

## 1. Setup

Tested on Linux with PulseAudio/PipeWire, Python 3.10, CPU-only (no GPU).

### System prerequisite
PortAudio runtime (for microphone/monitor capture):
```bash
sudo apt install libportaudio2        # Debian/Ubuntu
```

### Python environment
```bash
python3 -m venv .venv
source .venv/bin/activate

# CPU builds of torch/torchaudio from the PyTorch CPU index:
pip install torch==2.14.1 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
```

### Models (downloaded + converted once)
```bash
./setup_models.sh
```
This downloads the Armenian Whisper and NLLB models and converts them to
CTranslate2 int8 under `models/` (gitignored — weights are **not** committed). The
ECAPA speaker model downloads automatically on first run. Total on-disk ≈ 1 GB.

---

## 2. Audio device setup

On Linux, "system audio" (the other side of a call) is the **monitor** of your
output sink — PulseAudio exposes it as a capture source ending in `.monitor`, so
**no virtual loopback device (BlackHole etc.) is needed**. You keep hearing the
call normally; we just read a copy of what's being played.

List the available sources (the tool suggests sensible defaults):
```bash
python main.py --list-devices
```
You'll see your microphone and one `*.monitor` per output. By default the program
uses your **default microphone** and the **default sink's monitor**. Override with
name substrings if needed:
```bash
python main.py --run --mic "Digital Microphone" --system "Speaker"
```

Check both sources are being captured (watch both bars move — speak, and play
something):
```bash
python main.py --meter
```

---

## 3. Running

```bash
python main.py --run                 # the main program
python main.py --run --timing        # also show per-segment latency breakdown
```
Useful flags:
- `--mic <sel>` / `--system <sel>` — pick sources by name substring (see `--list-devices`).
- `--speaker-threshold <0..1>` — cosine threshold for "same speaker" on the system
  side (default `0.40`; raise to split more eagerly, lower to merge more).
- `--max-utterance <sec>` — soft-cut cap for long speech (default `8`).

Other modes (used while building/validating):
- `--meter` — live RMS meter for both sources (step 1 check).
- `--vad` — print each segmented utterance as it closes (step 2 check).
- `--record out.wav --seconds N` — record mic audio to a WAV (for benchmarking).

Stop with Ctrl-C.

---

## 4. Models — what we chose and why

Armenian is low-resource, and the machine is a **no-GPU, 2-core i3** laptop, so
every choice trades quality against the ~5 s latency budget. The guiding number is
**R** = compute-seconds per audio-second (benchmark with `bench_asr.py`).

| Stage | Model | Why |
|---|---|---|
| **ASR** | [`Chillarmo/whisper-small-armenian-v2`](https://huggingface.co/Chillarmo/whisper-small-armenian-v2), CT2 int8 | Fine-tuned Whisper-small (244M), ~24% WER on Common Voice 20, Apache-2.0. Plain `whisper-small` is ~38% WER, so the fine-tune is a large free win. Measured **R≈0.25** here → a 10 s utterance transcribes in ~2.5 s, well inside budget. |
| **Translation** | NLLB-200 distilled-600M, CT2 int8 (`hye_Armn`→`eng_Latn`) | Strong low-resource coverage incl. Armenian; ~0.3–0.7 s per short utterance on CPU. |
| **Speaker ID** | [`speechbrain/spkrec-ecapa-voxceleb`](https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb) + online cosine clustering | Light ECAPA-TDNN embeddings (~0.1–0.25 s/utterance); nearest-centroid matching keeps labels stable within a session without heavyweight offline diarisation. |
| **VAD / endpointing** | [silero-vad](https://github.com/snakers4/silero-vad) | Tiny, fast, accurate; defines "end of phrase" via trailing silence. |

### Alternatives considered
- **ASR up-tier:** `Chillarmo/whisper-large-v3-turbo-armenian` (~15% WER) and
  BuzzASR large-v3 (~7.5% WER on its own test set) are more accurate, but too slow
  for ~5 s real-time on this CPU. Easy to swap in on stronger hardware.
- **ASR down-tier:** plain `whisper-base` — kept as a fallback if a slower machine
  can't hold budget. Not needed here (small fits comfortably).
- **Translation:** `Helsinki-NLP/opus-mt-hy-en` (Apache-2.0, ~300 MB, faster) is a
  lighter option; NLLB was chosen for its generally stronger low-resource quality.
  Whisper's own `translate` task was rejected — it loses the Armenian transcript and
  gives less control.
- **Speaker ID:** pyannote streaming diarisation — more capable but far heavier;
  not worth the CPU/latency on a no-GPU i3.

---

## 5. Architecture

A small threaded pipeline with clear stage boundaries, connected by queues so a slow
stage never blocks capture:

```
mic  ─┐ capture (PortAudio)                     per-source silero VAD
       ├─────────────► block queue ──► VAD thread ──► utterance queue
system┘ (sink .monitor)                                     │
                                                            ▼
                                   worker: speaker-ID → ASR(hy) → MT(en) → printed line
```

- **`audio.py`** — enumerates Pulse sources and opens two capture streams. Capturing
  a specific mic *and* the sink monitor in one process uses the PortAudio `pulse`
  device plus `PULSE_SOURCE` set at stream-open time (Pulse binds the source then).
- **`vad.py`** — `StreamingVAD` per source: trailing-silence endpointing, pre-roll
  padding, min-speech filtering, and max-length soft-cut to bound worst-case latency.
- **`asr.py`** — faster-whisper wrapper, latency-tuned (greedy, no temperature
  fallback, no previous-text conditioning, Whisper's own VAD off).
- **`translate.py`** — NLLB on CTranslate2 (`hye_Armn`→`eng_Latn`, greedy).
- **`speakers.py`** — ECAPA embeddings + online cosine clustering; `Me` for the mic.
- **`pipeline.py`** — threads + queues; a **single FIFO worker** keeps ASR serialized
  (two concurrent ASR jobs would just halve each other's cores on 2 physical cores)
  and preserves output order.
- **`main.py`** — CLI and device selection.

### Latency
Budget is measured from end-of-phrase to the printed English line. The trailing
silence used to detect the endpoint (~0.5 s) counts against it, leaving ~4 s of
compute. On this machine a typical utterance measures **asr ≈ 2 s + mt ≈ 0.3 s +
speaker ≈ 0.15 s ≈ 2.5 s compute, ~3.6 s total — comfortably under 5 s.** `cpu_threads`
is set to **2** (benchmarked faster than 4 — hyperthreads hurt here). Run with
`--timing` to see the per-segment breakdown.

---

## 6. Known limitations & what I'd improve with more time

- **ASR accuracy.** Whisper-small-hy (~24% WER) mishears some words, especially on
  quiet or fast speech. With more compute, the large-v3-turbo fine-tune would help.
- **Speaker clustering is unsupervised and threshold-sensitive.** Short/quiet
  utterances depress same-speaker similarity (observed ~0.50), so the default
  threshold is a conservative 0.40, tunable via `--speaker-threshold`. Very short
  utterances or similar-sounding voices can be mislabelled; labels don't persist
  across sessions (out of scope).
- **No echo handling** (out of scope): if speakers are loud enough to enter the mic,
  the same speech may appear on both sides.
- **Long monologues** are soft-cut at ~8 s, which can split a sentence across lines.
- **Translation** occasionally drops/garbles words when the ASR text is wrong —
  errors compound across the two stages.
- **Next steps:** streaming partial hypotheses for lower perceived latency; a second
  worker to pipeline ASR/MT if moved to stronger hardware; light punctuation/casing
  cleanup; per-speaker colour in the terminal.

---

## 7. Repo layout
```
main.py          CLI + device selection + run loop
audio.py         capture streams + source enumeration + WAV I/O
vad.py           silero-vad streaming segmentation
asr.py           faster-whisper (Armenian)
translate.py     NLLB hy->en (CTranslate2)
speakers.py      ECAPA embeddings + online clustering
pipeline.py      threads + queues tying the stages together
bench_asr.py     R benchmark (model vs latency)
bench_translate.py  ASR+MT end-to-end check
setup_models.sh  one-time model download + CT2 conversion
chats/           AI chat transcripts (process record)
```
