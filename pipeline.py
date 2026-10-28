"""Threaded pipeline wiring the stages into a live transcriber (build step 6).

    capture (2 Pulse streams)         # PortAudio callback threads
        -> block_queue
    VAD thread                        # one silero segmenter per source
        -> utterance_queue
    worker thread                     # speaker-ID -> ASR -> MT, in FIFO order
        -> printer (prints the line)

A single worker keeps ASR serialized (the plan's choice for a 2-core CPU: two
concurrent ASR jobs would just halve each other's cores) and preserves output
order. With R~0.25 there is ample headroom under the 5 s budget, so we favour
simplicity over fine-grained stage parallelism.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass

import audio
import config
import vad


@dataclass
class Result:
    speaker: str
    hy: str
    en: str
    t_end: float        # monotonic end-of-speech (start of the 5 s clock)
    t_print: float      # monotonic when ready to print
    asr_ms: float
    mt_ms: float
    spk_ms: float
    soft_cut: bool


class Transcriber:
    def __init__(self, mic_name: str, sys_name: str, *, speaker_threshold: float,
                 max_utterance_s: float = 8.0, timing: bool = False,
                 on_result=None):
        self.mic_name = mic_name
        self.sys_name = sys_name
        self.timing = timing
        self.on_result = on_result or self._default_print

        self._block_q: "queue.Queue" = queue.Queue()
        # Bounded so a CPU stall surfaces as back-pressure rather than silent lag.
        self._utt_q: "queue.Queue" = queue.Queue(maxsize=16)
        self._stop = threading.Event()
        self._t0 = time.monotonic()

        # Models (loaded in start(), so construction is cheap/importable).
        self._segmenters: dict = {}
        self._asr = None
        self._mt = None
        self._spk = None
        self._max_utterance_s = max_utterance_s
        self._speaker_threshold = speaker_threshold

        self._mic_stream = audio.CaptureStream(mic_name, config.SOURCE_ME, self._block_q)
        self._sys_stream = audio.CaptureStream(sys_name, config.SOURCE_SYSTEM, self._block_q)
        self._vad_thread = threading.Thread(target=self._vad_loop, daemon=True)
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)

    # ---- model loading -------------------------------------------------
    def load_models(self, log=print) -> None:
        import asr
        import speakers
        import translate

        log("Loading models (ASR, translation, speaker ID)...")
        self._asr = asr.ArmenianASR()
        self._mt = translate.Translator()
        self._spk = speakers.SpeakerIdentifier(threshold=self._speaker_threshold)
        for tag in (config.SOURCE_ME, config.SOURCE_SYSTEM):
            self._segmenters[tag] = vad.StreamingVAD(
                tag, vad.load_vad_prob_fn(), max_utterance_s=self._max_utterance_s)
        log("Warming up...")
        self._asr.warmup(); self._mt.warmup(); self._spk.warmup()
        log("Ready.\n")

    # ---- threads -------------------------------------------------------
    def _vad_loop(self) -> None:
        while not self._stop.is_set():
            try:
                tag, block, _ = self._block_q.get(timeout=0.2)
            except queue.Empty:
                continue
            if block.shape[0] != config.BLOCK_SIZE:
                continue
            utt = self._segmenters[tag].feed(block)
            if utt is not None:
                try:
                    self._utt_q.put_nowait(utt)
                except queue.Full:
                    print("[pipeline] CPU behind — dropping an utterance to stay live",
                          flush=True)

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                utt = self._utt_q.get(timeout=0.2)
            except queue.Empty:
                continue

            t = time.monotonic()
            speaker = self._spk.identify(utt.source_tag, utt.audio)
            spk_ms = (time.monotonic() - t) * 1000.0

            tr = self._asr.transcribe(utt.audio)
            if not tr.text.strip():
                continue  # nothing recognised (silence / noise)
            tl = self._mt.translate(tr.text)

            self.on_result(Result(
                speaker=speaker, hy=tr.text, en=tl.text,
                t_end=utt.t_end, t_print=time.monotonic(),
                asr_ms=tr.asr_ms, mt_ms=tl.mt_ms, spk_ms=spk_ms,
                soft_cut=utt.soft_cut,
            ))

    # ---- lifecycle -----------------------------------------------------
    def start(self) -> None:
        self._mic_stream.start()
        self._sys_stream.start()
        self._vad_thread.start()
        self._worker_thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._mic_stream.stop()
        self._sys_stream.stop()
        self._vad_thread.join(timeout=1.0)
        self._worker_thread.join(timeout=2.0)

    # ---- default output ------------------------------------------------
    def _default_print(self, r: Result) -> None:
        stamp = time.strftime("%H:%M:%S", time.gmtime(r.t_end - self._t0))
        line = f"[{stamp}] {r.speaker}\nHY: {r.hy}\nEN: {r.en}"
        if self.timing:
            latency = r.t_print - r.t_end
            flag = " soft-cut" if r.soft_cut else ""
            line += (f"\n(latency {latency:.2f}s | asr {r.asr_ms:.0f}ms "
                     f"mt {r.mt_ms:.0f}ms spk {r.spk_ms:.0f}ms{flag})")
        print(line + "\n", flush=True)
