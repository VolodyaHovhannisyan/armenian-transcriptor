"""Shared configuration constants for the live Armenian transcriber.

Kept deliberately small; audio-format values are fixed by the downstream models
(Whisper and silero-vad both expect 16 kHz mono float32).
"""

# Audio format used end-to-end. Whisper and silero-vad both operate at 16 kHz mono.
SAMPLE_RATE = 16_000
CHANNELS = 1
DTYPE = "float32"

# Capture block size (frames per callback). ~32 ms at 16 kHz: small enough for
# responsive VAD, large enough to keep callback overhead low.
BLOCK_SIZE = 512

# PortAudio device used for PulseAudio/PipeWire routing. We select the actual
# source (mic vs sink-monitor) via the PULSE_SOURCE env var at stream-open time.
PULSE_DEVICE = "pulse"

# Source tags carried alongside captured audio through the pipeline.
SOURCE_ME = "me"          # microphone -> always labelled "Me"
SOURCE_SYSTEM = "system"  # sink monitor -> diarised into Speaker 1, 2, ...
