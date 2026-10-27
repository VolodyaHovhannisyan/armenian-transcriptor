"""Live Armenian -> English transcriber (terminal).

Build step 1: audio device selection + simultaneous mic/system capture with a
live RMS meter, to prove the two-source capture works before adding VAD/ASR/MT.

Usage:
  python main.py --list-devices          # show PulseAudio sources + PortAudio devices
  python main.py --meter                 # live level meter for mic + system monitor
  python main.py --meter --mic <sel> --system <sel>   # pick sources by name substring
"""

from __future__ import annotations

import argparse
import queue
import sys
import time

import audio
import config


def _resolve_sources(mic_sel, sys_sel):
    """Resolve mic/system selectors to Pulse source names (shared by meter + vad)."""
    sources = audio.list_pulse_sources()
    if not sources:
        return None, None, "No PulseAudio sources found."
    mic = audio.resolve_source(mic_sel, sources) if mic_sel else audio.default_source()
    sysm = audio.resolve_source(sys_sel, sources) if sys_sel else audio.default_sink_monitor()
    if not mic:
        return None, None, "Could not determine a microphone source; pass --mic."
    if not sysm:
        return None, None, "Could not determine a system monitor source; pass --system."
    return mic, sysm, None


def cmd_list_devices() -> int:
    sources = audio.list_pulse_sources()
    if not sources:
        print("No PulseAudio sources found (is `pactl` available / Pulse running?).")
    else:
        mic = audio.default_source()
        mon = audio.default_sink_monitor()
        print("PulseAudio sources (use the Name with --mic / --system):\n")
        for s in sources:
            tags = []
            if s.is_monitor:
                tags.append("MONITOR / system audio")
            if s.name == mic:
                tags.append("default mic")
            if s.name == mon:
                tags.append("default sink monitor")
            tag = f"   <- {', '.join(tags)}" if tags else ""
            print(f"  {s.description}\n    {s.name}{tag}\n")
        print(f"Suggested  --mic    {mic or '(none)'}")
        print(f"Suggested  --system {mon or '(none)'}")
    return 0


def _bar(level: float, width: int = 30) -> str:
    # Map RMS (~0..0.3 typical speech) to a bar; clamp at 1.0.
    filled = min(width, int(level / 0.3 * width))
    return "#" * filled + "-" * (width - filled)


def cmd_meter(mic_sel: str | None, sys_sel: str | None) -> int:
    mic_name, sys_name, err = _resolve_sources(mic_sel, sys_sel)
    if err:
        print(err)
        return 1

    print(f"Mic    : {mic_name}")
    print(f"System : {sys_name}")
    print("\nSpeak into the mic and play some audio. Ctrl-C to stop.\n")

    q: "queue.Queue" = queue.Queue()
    mic_stream = audio.CaptureStream(mic_name, config.SOURCE_ME, q)
    sys_stream = audio.CaptureStream(sys_name, config.SOURCE_SYSTEM, q)

    levels = {config.SOURCE_ME: 0.0, config.SOURCE_SYSTEM: 0.0}
    try:
        mic_stream.start()
        sys_stream.start()
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to open streams: {exc}")
        return 1

    last_draw = 0.0
    try:
        while True:
            try:
                tag, block, _ = q.get(timeout=0.5)
            except queue.Empty:
                continue
            # Smooth the level a little for readability.
            levels[tag] = max(audio.rms(block), levels[tag] * 0.6)
            now = time.monotonic()
            if now - last_draw >= 0.05:
                sys.stdout.write(
                    f"\r  Me     |{_bar(levels[config.SOURCE_ME])}|   "
                    f"Speaker|{_bar(levels[config.SOURCE_SYSTEM])}| "
                )
                sys.stdout.flush()
                last_draw = now
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        mic_stream.stop()
        sys_stream.stop()
    return 0


def cmd_record(mic_sel: str | None, seconds: float, out: str) -> int:
    """Record `seconds` of mic audio to a 16 kHz mono WAV (for ASR benchmarking)."""
    import numpy as np

    # Only the mic is needed here; resolve it directly.
    sources = audio.list_pulse_sources()
    mic_name = audio.resolve_source(mic_sel, sources) if mic_sel else audio.default_source()
    if not mic_name:
        print("Could not determine a microphone source; pass --mic.")
        return 1

    print(f"Mic : {mic_name}")
    print(f"Recording {seconds:.0f}s to {out} — speak Armenian now...\n")

    q: "queue.Queue" = queue.Queue()
    stream = audio.CaptureStream(mic_name, config.SOURCE_ME, q)
    chunks: list = []
    stream.start()
    t0 = time.monotonic()
    try:
        while time.monotonic() - t0 < seconds:
            try:
                _tag, block, _ = q.get(timeout=0.3)
            except queue.Empty:
                continue
            chunks.append(block)
            elapsed = time.monotonic() - t0
            sys.stdout.write(f"\r  {elapsed:4.1f}s  level |{_bar(audio.rms(block))}| ")
            sys.stdout.flush()
    except KeyboardInterrupt:
        print("\nStopped early.")
    finally:
        stream.stop()

    if not chunks:
        print("\nNo audio captured.")
        return 1
    data = np.concatenate(chunks).astype("float32")
    audio.write_wav(out, data)
    print(f"\nSaved {len(data)/config.SAMPLE_RATE:.1f}s  peakRMS={audio.rms(data):.4f}  -> {out}")
    return 0


def cmd_vad(mic_sel: str | None, sys_sel: str | None) -> int:
    """Live segmentation check: print a line each time an utterance closes."""
    import vad

    mic_name, sys_name, err = _resolve_sources(mic_sel, sys_sel)
    if err:
        print(err)
        return 1

    print(f"Mic    : {mic_name}")
    print(f"System : {sys_name}")
    print("Loading silero-vad (one instance per source)...")
    # Each stateful model gets its own instance so interleaved sources don't corrupt state.
    segmenters = {
        config.SOURCE_ME: vad.StreamingVAD(config.SOURCE_ME, vad.load_vad_prob_fn()),
        config.SOURCE_SYSTEM: vad.StreamingVAD(config.SOURCE_SYSTEM, vad.load_vad_prob_fn()),
    }
    print("Ready. Speak / play Armenian audio; utterances print on end-of-phrase. Ctrl-C to stop.\n")

    q: "queue.Queue" = queue.Queue()
    mic_stream = audio.CaptureStream(mic_name, config.SOURCE_ME, q)
    sys_stream = audio.CaptureStream(sys_name, config.SOURCE_SYSTEM, q)
    try:
        mic_stream.start()
        sys_stream.start()
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to open streams: {exc}")
        return 1

    t0 = time.monotonic()
    try:
        while True:
            try:
                tag, block, _ = q.get(timeout=0.5)
            except queue.Empty:
                continue
            if block.shape[0] != config.BLOCK_SIZE:
                continue  # silero needs exactly 512-sample frames
            utt = segmenters[tag].feed(block)
            if utt is not None:
                label = "Me" if tag == config.SOURCE_ME else "Speaker?"
                cut = "  [soft-cut]" if utt.soft_cut else ""
                stamp = time.strftime("%H:%M:%S", time.gmtime(utt.t_end - t0))
                print(f"[{stamp}] {label:9s} utterance {utt.duration:.2f}s{cut}")
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        mic_stream.stop()
        sys_stream.stop()
    return 0


def cmd_run(mic_sel, sys_sel, *, timing, speaker_threshold, max_utterance) -> int:
    """Live transcription: capture both sources and print HY + EN per speaker."""
    import pipeline

    mic_name, sys_name, err = _resolve_sources(mic_sel, sys_sel)
    if err:
        print(err)
        return 1

    print(f"Mic (Me)     : {mic_name}")
    print(f"System (call): {sys_name}\n")

    t = pipeline.Transcriber(
        mic_name, sys_name,
        speaker_threshold=speaker_threshold,
        max_utterance_s=max_utterance,
        timing=timing,
    )
    t.load_models()
    print("Listening. Speak Armenian / play the call audio. Ctrl-C to stop.\n")
    t.start()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        t.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Live Armenian -> English transcriber")
    p.add_argument("--list-devices", action="store_true",
                   help="list PulseAudio sources and exit")
    p.add_argument("--meter", action="store_true",
                   help="live RMS meter for mic + system (build step 1 check)")
    p.add_argument("--vad", action="store_true",
                   help="live VAD segmentation: print each closed utterance (step 2 check)")
    p.add_argument("--record", metavar="OUT.wav",
                   help="record mic audio to a 16 kHz WAV (for ASR benchmarking)")
    p.add_argument("--seconds", type=float, default=8.0,
                   help="duration for --record (default 8)")
    p.add_argument("--run", action="store_true",
                   help="live transcription + translation (the main program)")
    p.add_argument("--timing", action="store_true",
                   help="with --run: print per-segment latency breakdown")
    p.add_argument("--speaker-threshold", type=float, default=0.40,
                   help="cosine threshold for same-speaker on the system side (default 0.40)")
    p.add_argument("--max-utterance", type=float, default=8.0,
                   help="max seconds before an utterance is soft-cut (default 8)")
    p.add_argument("--mic", help="mic source name or substring (default: system default)")
    p.add_argument("--system", help="system/monitor source name or substring "
                                     "(default: default sink monitor)")
    args = p.parse_args(argv)

    if args.list_devices:
        return cmd_list_devices()
    if args.meter:
        return cmd_meter(args.mic, args.system)
    if args.vad:
        return cmd_vad(args.mic, args.system)
    if args.record:
        return cmd_record(args.mic, args.seconds, args.record)
    if args.run:
        return cmd_run(args.mic, args.system, timing=args.timing,
                       speaker_threshold=args.speaker_threshold,
                       max_utterance=args.max_utterance)
    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
