# Live Armenian → English Transcriber (terminal)

> ## ⏸️ SESSION STATE — resume here
> **Status:** Plan approved. **ALL 7 BUILD STEPS DONE & verified. Core feature-complete.**
> Remaining (needs the user): live interview test with real 2-speaker Armenian audio; tune
> `--speaker-threshold` on that audio; export Claude chats into `chats/`; `git init` + first
> commit (not done — awaiting user). Files: config/audio/vad/asr/translate/speakers/pipeline/
> main .py + bench_*.py + setup_models.sh + README.md + requirements.txt + .gitignore + chats/.
> Verified: dual capture; VAD on real clip; ASR R=0.25 (small model, cpu_threads=2); HY→EN
> ~3.6 s < 5 s; speaker clustering (threshold 0.40); integrated pipeline produces labelled
> HY+EN; `--run` clean startup/shutdown. **Models in `models/` (gitignored):
> whisper-small-hy-ct2, nllb-600M-ct2, ecapa.**
> **Machine:** Linux, no GPU, 4-thread (2-core) i3-1115G4, 15 GB RAM, PulseAudio.
> **Decisions locked:** strict ≤ 5 s latency · separate NLLB-200 for hy→en · ECAPA + online
> clustering for speaker ID. **Models:** ASR `Chillarmo/whisper-small-armenian-v2` (CT2 int8),
> MT NLLB-200 distilled-600M, speaker `speechbrain/spkrec-ecapa-voxceleb`, VAD silero-vad.
>
> **Done so far:**
> - Project scaffolding: `config.py`, `audio.py`, `main.py`, `requirements.txt`, `.gitignore`.
> - `.venv` created; step-1 deps installed (sounddevice, numpy, rich). System lib
>   `libportaudio2` installed (user ran apt).
> - **Build step 1 (audio spike) complete & verified:** `--list-devices` enumerates Pulse
>   sources and auto-suggests mic + sink-monitor; dual mic+monitor capture works in one
>   process via the `PULSE_SOURCE`-at-open-time binding trick; monitor confirmed capturing
>   real playback (440 Hz tone, RMS 0.196). `--meter` command available for live check.
>
> **Key implementation note:** simultaneous capture of a specific mic source AND the sink
> monitor uses PortAudio device `"pulse"` + setting `PULSE_SOURCE` right before each
> `InputStream` open (PulseAudio binds the source at connect time). `sounddevice` is imported
> lazily so `--list-devices` works without PortAudio.
>
> **Build step 2 — VAD segmentation: DONE & fully verified (incl. real speech).** `vad.py`
> adds `StreamingVAD` (silero per source): endpoint 500 ms, pre-roll 150 ms, min-speech
> 200 ms, soft-cut cap (default 8 s). Emits `Utterance(source_tag, audio, t_end, duration,
> soft_cut)`. Wired into `main.py --vad`. Real clip `samples/hy_test.wav` segments into 2
> clean utterances (3.78 s + 1.60 s) — silero fires on real Armenian. One instance per source
> (model is stateful). Deps: CPU `torch` 2.14.1, `silero-vad`.
>
> **Build step 3 — ASR + R benchmark: DONE.** `asr.py` wraps faster-whisper (greedy, no temp
> fallback, no prev-text, Whisper-VAD off, warmup). Model `Chillarmo/whisper-small-armenian-v2`
> converted to CT2 int8 at `models/whisper-small-hy-ct2` (238 MB, gitignored). **Benchmark on
> the 10 s clip (`bench_asr.py`): R=0.25 @ cpu_threads=2 (beats 4 → hyperthreads hurt), max
> utterance ~12 s to stay <5 s. Small model CONFIRMED as default — no fallback to base
> needed.** `cpu_threads=2` set as default. Transcription readable Armenian
> ("Ո՞ղջույն, ես հիմա խոսում եմ հայերեն…"). Deps: `faster-whisper` 1.2.1, `ctranslate2` 4.8.4,
> `transformers` 5.20.2.
>
> **Build step 4 — Translation (hy→en): DONE.** `translate.py` wraps NLLB-200 distilled-600M
> on CTranslate2 int8 at `models/nllb-600M-ct2` (600 MB, gitignored) + HF tokenizer
> (sentencepiece), hye_Armn→eng_Latn, beam=1, warmup. **End-to-end on the clip: HY→EN
> understandable; asr 2.29 s + mt 0.66 s = 2.94 s compute, ~3.6 s total — well under 5 s.**
> Load+warmup ~29 s is one-time startup. Dep added: `sentencepiece`.
>
> **Build step 5 — Speaker ID: DONE.** `speakers.py`: mic → "Me"; system → ECAPA embeddings
> (`speechbrain/spkrec-ecapa-voxceleb`, auto-downloaded to `models/ecapa`, gitignored) +
> `OnlineSpeakerClustering` (nearest-centroid cosine, running-mean update). Embeddings
> discriminate well (same-speaker 0.50 vs noise 0.005). **Threshold defaulted to 0.40**
> (short/quiet utterances depress same-speaker cosine; must be a CLI flag for live tuning).
> Verified: same-speaker merges, distinct splits, labels stable. Deps: `speechbrain` 1.1.2,
> CPU `torchaudio` 2.11.0.
>
> **Next step on resume:** **Build step 6 — Integrate threaded pipeline.** `pipeline.py`:
> capture→block queue→VAD thread→utterance queue→worker (speaker+ASR+MT)→printer; `main.py run`
> with live output `[HH:MM:SS] Speaker N  HY: … EN: …`, `--timing` (measures t_print − t_end),
> `--speaker-threshold`, graceful Ctrl-C, bounded-queue back-pressure. Then step 7 = README +
> requirements + `chats/`.
> **Deliverables not to forget:** README (setup, device setup, model rationale, limits) and a
> `chats/` folder with full Claude transcripts; do **not** commit model weights.



## Context
This is the Mathrix AI coding challenge (`Mathrix_AI_Coding_Challenge.docx`): a Python
program that listens to a live conversation, transcribes spoken **Armenian**, prints an
**English** translation in the terminal **within 5 s**, labelled by speaker — like a video
call, but in the terminal.

Hard constraints from the brief:
- **Two simultaneous audio sources:** microphone (always labelled **Me**) + system audio
  output (the far side of the call → **Speaker 1/2/...**, consistent within a session).
- **Local models only** — no cloud speech/translation APIs.
- **Latency ≤ 5 s** from the end of a spoken phrase to its English line.
- Output per segment: `time · speaker · HY original · EN translation`.
- Armenian is low-resource; model choice must be deliberate and explained in the README.

Target machine (this laptop): **Linux, no GPU, 4-thread i3-1115G4, 15 GB RAM.** The CPU
budget — not the audio plumbing — is the central engineering challenge. On Linux, system
audio is captured from the PulseAudio/PipeWire `*.monitor` source of the output sink, so no
virtual loopback device is needed.

## Architecture
A small, threaded pipeline with clear stage boundaries (audio / VAD / ASR / MT / speaker /
output), connected by queues so slow stages can't block capture:

```
mic source  ──► capture ─► VAD segment ─┐                        ┌─► [Me]
                                        ├─► utterance queue ─► ASR(hy) ─► MT(en) ─► printer
monitor src ──► capture ─► VAD segment ─┘        │                                     ▲
                                                 └─► speaker-ID (monitor only) ────────┘
```

- **Audio capture** (`sounddevice`, PortAudio): two independent input streams, 16 kHz mono,
  float32. User picks devices via `--list-devices` printout and `--mic` / `--system` flags
  (index or name substring). Each stream carries a `source` tag (`me` / `system`).
- **Segmentation / endpointing** (`silero-vad`, local, tiny): detect speech, emit an
  "utterance" on a trailing silence (~500–700 ms). Endpoint silence is what defines "end of
  a spoken phrase" for the 5 s clock. Cap max utterance length (~12 s) so long speech still
  flushes.
- **ASR — Armenian:** default **`Chillarmo/whisper-small-armenian-v2`** (fine-tuned
  Whisper-small, 244M, ~24% WER / ~4.8% CER on Common Voice 20, Apache-2.0), converted to
  CTranslate2 `int8` and run via `faster-whisper` with `language="hy"`, `beam_size=1`,
  `condition_on_previous_text=False`. Chosen as the best Armenian quality that fits the ≤5 s
  budget on a no-GPU 2-core CPU (plain `openai/whisper-small` is ~38% WER). Must still pass
  the `R` benchmark (build step 3); if it misses budget, fall to `whisper-base`. Up-tier
  option for stronger hardware: `Chillarmo/whisper-large-v3-turbo-armenian` (~15% WER) via
  `--model large`.
- **Translation — hy→en:** default **NLLB-200 distilled-600M** via CTranslate2 `int8`
  (`hye_Armn`→`eng_Latn`), `beam_size=1`; good low-resource Armenian coverage. (NLLB is
  CC-BY-NC — acceptable for this non-commercial challenge.) Lighter alternative to benchmark
  on FLORES-200 hy-en: `Helsinki-NLP/opus-mt-hy-en` (Apache-2.0, ~300 MB, faster) — adopt if
  quality is close. Whisper's own `translate` task kept as a documented fallback only.
- **Speaker identification:**
  - Mic utterances → label **Me** directly (no modelling).
  - System utterances → ECAPA-TDNN speaker embedding (`speechbrain`) + **online clustering**:
    cosine-compare to known centroids; match → same label, else → new **Speaker N**. Light,
    streaming-friendly, labels stay stable within the session. (pyannote streaming kept as a
    heavier alternative in the README.)
- **Output** (`rich`): single printer consumes the result queue and prints one line per
  segment: `[HH:MM:SS] Speaker 1   HY: ...   EN: ...`. Timestamp = utterance end time.
- **Latency management:** worker(s) pull utterances FIFO; if the CPU falls behind, prefer
  staying real-time (log a dropped/queued warning) over unbounded lag. A `--timing` flag
  prints per-stage ms to prove the ≤5 s budget.

## Latency design (the ≤ 5 s path)
The clock starts when the speaker stops talking. We only detect that *after* a trailing
silence, so that silence is spent from the budget. The realistic per-utterance decomposition:

```
5 s budget ≈ silence_endpoint (~0.5 s) + ASR_compute + MT_compute + embed + overhead + safety
```

So the actual compute budget is only ~**4 s per utterance**, and ASR dominates it. The single
most important lever is therefore **bounding utterance audio length**, because faster-whisper
compute scales with audio duration.

**1. Measure R first (benchmark gate).** Before choosing anything, measure `R` =
compute-seconds per audio-second for each Whisper size (base/small/medium) in `int8` on this
CPU, warm. Then the safe max utterance length is `max_audio ≈ (5 − 0.5 − MT − embed − 0.7
safety) / R`. Pick the largest model whose `max_audio` is still a usable phrase length
(≈ 4–6 s). This turns "strict ≤ 5 s" into a concrete model + cap decision backed by numbers.

**2. Cap + soft-cut long speech.** VAD force-closes an utterance at `max_audio` by cutting at
the nearest micro-pause, so a long monologue is emitted in budget-sized pieces instead of
making us wait (and then blow the budget) for the whole thing. Bounds worst-case latency.

**3. Tune endpointing.** silero-vad trailing-silence ~400–500 ms (configurable): shorter =
lower latency, higher risk of clipping a sentence. Expose `--endpoint-ms`.

**4. ASR configured for speed, not peak accuracy.** `beam_size=1`, `best_of=1`,
`temperature=0`, `condition_on_previous_text=False`, no word timestamps, `vad_filter=False`
(we already segmented), `compute_type=int8`. Capture at 16 kHz mono to avoid resampling.

**5. Respect the 2 physical cores — avoid thread oversubscription.** Pin CTranslate2 thread
counts (`cpu_threads`, `OMP_NUM_THREADS`) so ASR and MT don't each spawn 4 threads and thrash.
Benchmark `cpu_threads` 2 vs 4 (hyperthreads often hurt here). Serialize ASR as a single FIFO
worker (mic and system turns are sequential in a conversation; echo is explicitly out of
scope), rather than running two ASR jobs that halve each other's cores.

**6. Pipeline ASR → MT as separate stages.** ASR worker hands text to an MT worker and
immediately starts the next utterance, so MT(prev) overlaps capture/VAD and improves
throughput under load, at negligible per-item cost (MT ≪ ASR).

**7. Compute speaker embedding off the ASR critical path.** ECAPA runs on raw audio,
independent of ASR; schedule it so it doesn't contend with ASR for cores (e.g. after ASR
hands off, before/with MT). It's cheap (~100–300 ms) and only for system-side utterances.

**8. Warm up at startup.** Load all models once and run a dummy inference during init so the
first real phrase isn't penalised by lazy init — matters for the live demo's first line.

**9. Back-pressure, not lag.** Bounded queues; if the backlog grows (CPU fell behind), log a
visible warning rather than silently accumulating multi-second lag. Length-capping (#2) makes
pile-ups unlikely in normal turn-taking.

**10. Prove it.** `--timing` stamps `t_end` (VAD endpoint) and `t_print`, and prints the
per-stage breakdown (endpoint wait / ASR / MT / embed / queue). The reported metric is
`t_print − t_end`; target median ≤ 5 s on this machine.

**Streaming ASR is deliberately out of default scope:** the budget is measured from end of
phrase, so endpoint-triggered chunking meets it without the extra CPU and complexity of
partial-hypothesis streaming (noted as a future improvement in the README).

## Proposed layout
```
armenian-transcriptor/
  main.py              # CLI, device selection, wiring, shutdown
  audio.py             # capture streams + resampling + source tagging
  vad.py               # silero-vad segmentation / endpointing
  asr.py               # faster-whisper wrapper (hy)
  translate.py         # NLLB (ct2) wrapper (hy->en)
  speakers.py          # embedding + online clustering; Me vs Speaker N
  output.py            # rich terminal printer
  pipeline.py          # queues + worker threads tying stages together
  requirements.txt
  README.md            # setup, device setup, model choices + trade-offs, limits
  chats/               # exported Claude Cowork transcripts (required deliverable)
```
Models are downloaded at first run / documented; **weights are not committed** (gitignore).

## Build order (incremental, test each stage)
1. **Audio spike:** list devices, open mic + monitor streams, confirm both capture (RMS
   meter). Nail the Linux device-picking UX first — it's what's demoed live.
2. **VAD segmentation:** clean utterance boundaries on both sources.
3. **ASR:** wire faster-whisper; print raw Armenian. Benchmark model sizes for the latency
   budget on this CPU and pick the default.
4. **Translation:** add NLLB hy→en; print HY + EN.
5. **Speaker ID:** Me for mic; online clustering for system side.
6. **Integrate + harden:** threaded pipeline, timing instrumentation, graceful Ctrl-C,
   back-pressure behaviour.
7. **README + requirements**, and collect `chats/`.

## Verification
- **Latency:** `--timing` reports end-of-speech → printed-line per segment; confirm median
  ≤ 5 s on this machine with the chosen model. This drives the model-size decision.
- **Two sources:** play Armenian audio through speakers (→ monitor/Speaker N) while speaking
  into the mic (→ Me); confirm both lines appear, correctly labelled.
- **Speaker consistency:** two distinct voices on the system side keep stable Speaker 1/2
  labels across a session.
- **Quality sanity:** Armenian transcript is recognisable and English is understandable on a
  few known clips; README documents honest limits.
- **End-to-end:** hold a short two-person Armenian conversation (the interview scenario) and
  watch the live terminal output.

## Decisions (locked)
- **Latency policy: strict ≤ 5 s.** If the CPU can't hit budget at good quality, drop to a
  smaller Whisper model and accept rougher output; never overrun. README documents the
  trade-off honestly.
- **Translation: separate NLLB-200 distilled-600M** (faster-whisper → Armenian, NLLB →
  English). Whisper translate task kept only as a documented fallback.
- **Speaker ID: ECAPA embeddings + online clustering** on the system side; Me for the mic.
  pyannote not used (too heavy for a no-GPU i3).
- **Concrete models chosen:** ASR `Chillarmo/whisper-small-armenian-v2` (CT2 int8, fallback
  `whisper-base`, up-tier `whisper-large-v3-turbo-armenian`); MT NLLB-200 distilled-600M (CT2
  int8; alt `Helsinki-NLP/opus-mt-hy-en`); speaker `speechbrain/spkrec-ecapa-voxceleb`; VAD
  silero-vad. All local, ~1 GB total, fits 15 GB RAM.
