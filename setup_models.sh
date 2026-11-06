#!/usr/bin/env bash
# Download and convert the ASR + translation models to CTranslate2 int8.
# Run once after installing requirements. Weights land in models/ (gitignored).
#
# By default this converts the fast defaults (small ASR + NLLB-600M). Pass
# --with-uptier to ALSO convert the higher-quality models used by
# `--asr-model turbo` and `--mt-model 1.3B` (bigger downloads, slower convert).
set -euo pipefail
cd "$(dirname "$0")"

WITH_UPTIER=0
[ "${1:-}" = "--with-uptier" ] && WITH_UPTIER=1

echo "[1/2] Converting Armenian Whisper small (ASR) -> CT2 int8 ..."
ct2-transformers-converter \
  --model Chillarmo/whisper-small-armenian-v2 \
  --output_dir models/whisper-small-hy-ct2 \
  --quantization int8 --force

echo "[2/2] Converting NLLB-200 distilled-600M (translation) -> CT2 int8 ..."
ct2-transformers-converter \
  --model facebook/nllb-200-distilled-600M \
  --output_dir models/nllb-600M-ct2 \
  --quantization int8 --force

if [ "$WITH_UPTIER" = "1" ]; then
  echo "[uptier 1/2] Converting whisper-large-v3-turbo-armenian (ASR) -> CT2 int8 ..."
  ct2-transformers-converter \
    --model Chillarmo/whisper-large-v3-turbo-armenian \
    --output_dir models/whisper-turbo-hy-ct2 \
    --quantization int8 --force

  # The turbo (large-v3) source repo ships no preprocessor_config.json, so
  # faster-whisper would default to 80 mel bins and crash — large-v3 needs 128.
  # Write the standard large-v3 feature-extractor config so inference works.
  cat > models/whisper-turbo-hy-ct2/preprocessor_config.json <<'JSON'
{
  "chunk_length": 30,
  "feature_extractor_type": "WhisperFeatureExtractor",
  "feature_size": 128,
  "hop_length": 160,
  "n_fft": 400,
  "n_samples": 480000,
  "nb_max_frames": 3000,
  "padding_side": "right",
  "padding_value": 0.0,
  "processor_class": "WhisperProcessor",
  "return_attention_mask": false,
  "sampling_rate": 16000
}
JSON

  echo "[uptier 2/2] Converting NLLB-200 distilled-1.3B (translation) -> CT2 int8 ..."
  ct2-transformers-converter \
    --model facebook/nllb-200-distilled-1.3B \
    --output_dir models/nllb-1.3B-ct2 \
    --quantization int8 --force
fi

echo "Done. (The ECAPA speaker model downloads automatically on first run.)"
echo "Benchmark the up-tier ASR:  python bench_asr.py samples/hy_test.wav turbo"
