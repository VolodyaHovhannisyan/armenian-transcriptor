"""Armenian -> English translation via NLLB-200 on CTranslate2 (build step 4).

Uses `facebook/nllb-200-distilled-600M` converted to CT2 int8 for speed on CPU,
with the HF tokenizer for (de)tokenisation. Source language is fixed to Armenian
(`hye_Armn`) and target to English (`eng_Latn`). Greedy decoding (beam_size=1)
keeps per-utterance latency low; MT is a small fraction of the ASR cost.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import config  # noqa: F401  (kept for symmetry / future tuning knobs)

DEFAULT_MODEL_DIR = "models/nllb-600M-ct2"
HF_TOKENIZER = "facebook/nllb-200-distilled-600M"
SRC_LANG = "hye_Armn"
TGT_LANG = "eng_Latn"


@dataclass
class Translation:
    text: str       # English text
    mt_ms: float    # wall-clock compute time


class Translator:
    def __init__(
        self,
        model_path: str = DEFAULT_MODEL_DIR,
        *,
        cpu_threads: int = 2,
    ):
        import ctranslate2
        from transformers import AutoTokenizer

        self.tokenizer = AutoTokenizer.from_pretrained(HF_TOKENIZER, src_lang=SRC_LANG)
        self.translator = ctranslate2.Translator(
            model_path, device="cpu", compute_type="int8",
            inter_threads=1, intra_threads=cpu_threads,
        )

    def translate(self, text_hy: str) -> Translation:
        text_hy = text_hy.strip()
        if not text_hy:
            return Translation(text="", mt_ms=0.0)

        t0 = time.monotonic()
        source = self.tokenizer.convert_ids_to_tokens(self.tokenizer.encode(text_hy))
        results = self.translator.translate_batch(
            [source],
            target_prefix=[[TGT_LANG]],
            beam_size=1,
        )
        target = results[0].hypotheses[0]
        if target and target[0] == TGT_LANG:
            target = target[1:]  # drop the forced language token
        text_en = self.tokenizer.decode(
            self.tokenizer.convert_tokens_to_ids(target),
            skip_special_tokens=True,
        ).strip()
        return Translation(text=text_en, mt_ms=(time.monotonic() - t0) * 1000.0)

    def warmup(self) -> None:
        self.translate("բարև")
