"""Armenian -> English translation via NLLB-200 on CTranslate2 (build step 4).

Uses `facebook/nllb-200-distilled-600M` converted to CT2 int8 for speed on CPU,
with the HF tokenizer for (de)tokenisation. Source language is fixed to Armenian
(`hye_Armn`) and target to English (`eng_Latn`). Greedy decoding (beam_size=1)
keeps per-utterance latency low; MT is a small fraction of the ASR cost.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

# Armenian sentence terminator `։` (U+0589) plus Latin ./!/? as a fallback.
_SENT_SPLIT = re.compile(r"(?<=[։.!?])\s+")


def _split_sentences(text: str) -> list[str]:
    parts = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
    return parts or [text]

import config  # noqa: F401  (kept for symmetry / future tuning knobs)

# Named MT presets -> (CT2 dir, HF tokenizer). 600M is the light default; 1.3B is
# the quality up-tier (better low-resource hy->en). MT is off the ASR critical
# path and a small fraction of the budget, so 1.3B + beam search is usually free.
MODELS = {
    "600M": ("models/nllb-600M-ct2", "facebook/nllb-200-distilled-600M"),
    "1.3B": ("models/nllb-1.3B-ct2", "facebook/nllb-200-distilled-1.3B"),
}
DEFAULT_MODEL = "600M"
DEFAULT_MODEL_DIR = MODELS[DEFAULT_MODEL][0]
SRC_LANG = "hye_Armn"
TGT_LANG = "eng_Latn"


@dataclass
class Translation:
    text: str       # English text
    mt_ms: float    # wall-clock compute time


class Translator:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        cpu_threads: int = 2,
        beam_size: int = 5,  # was greedy (1); MT is cheap, so beam search is ~free quality
    ):
        import ctranslate2
        from transformers import AutoTokenizer

        model_path, hf_tokenizer = MODELS.get(model, (model, model))
        self.beam_size = beam_size
        self.tokenizer = AutoTokenizer.from_pretrained(hf_tokenizer, src_lang=SRC_LANG)
        self.translator = ctranslate2.Translator(
            model_path, device="cpu", compute_type="int8",
            inter_threads=1, intra_threads=cpu_threads,
        )

    def translate(self, text_hy: str) -> Translation:
        text_hy = text_hy.strip()
        if not text_hy:
            return Translation(text="", mt_ms=0.0)

        t0 = time.monotonic()
        # Translate sentence-by-sentence as a batch: NLLB handles complete
        # sentences better than a long comma-spliced run-on.
        sentences = _split_sentences(text_hy)
        sources = [
            self.tokenizer.convert_ids_to_tokens(self.tokenizer.encode(s))
            for s in sentences
        ]
        results = self.translator.translate_batch(
            sources,
            target_prefix=[[TGT_LANG]] * len(sources),
            beam_size=self.beam_size,
        )
        parts = []
        for res in results:
            target = res.hypotheses[0]
            if target and target[0] == TGT_LANG:
                target = target[1:]  # drop the forced language token
            parts.append(self.tokenizer.decode(
                self.tokenizer.convert_tokens_to_ids(target),
                skip_special_tokens=True,
            ).strip())
        text_en = " ".join(p for p in parts if p).strip()
        return Translation(text=text_en, mt_ms=(time.monotonic() - t0) * 1000.0)

    def warmup(self) -> None:
        self.translate("բարև")
