#!/usr/bin/env bash
# Download and convert the ASR + translation models to CTranslate2 int8.
# Run once after installing requirements. Weights land in models/ (gitignored).
set -euo pipefail
cd "$(dirname "$0")"

echo "[1/2] Converting Armenian Whisper (ASR) -> CT2 int8 ..."
ct2-transformers-converter \
  --model Chillarmo/whisper-small-armenian-v2 \
  --output_dir models/whisper-small-hy-ct2 \
  --quantization int8 --force

echo "[2/2] Converting NLLB-200 distilled-600M (translation) -> CT2 int8 ..."
ct2-transformers-converter \
  --model facebook/nllb-200-distilled-600M \
  --output_dir models/nllb-600M-ct2 \
  --quantization int8 --force

echo "Done. (The ECAPA speaker model downloads automatically on first run.)"
