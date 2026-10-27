"""Speaker identification (build step 5).

Microphone audio is always labelled "Me". System-audio utterances (the far side
of the call) are told apart with ECAPA-TDNN speaker embeddings and *online*
cosine clustering: each new utterance is compared to the running centroids of the
speakers seen so far; a close match keeps that speaker's label, otherwise a new
"Speaker N" is created. This keeps labels stable within a session without needing
offline diarisation (see PLAN "Speaker identification").
"""

from __future__ import annotations

import warnings

import numpy as np

import config

ECAPA_SOURCE = "speechbrain/spkrec-ecapa-voxceleb"
ECAPA_SAVEDIR = "models/ecapa"


def _l2(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


class EcapaEmbedder:
    """Wraps SpeechBrain ECAPA-TDNN; maps 16 kHz mono audio -> L2-normalised vector."""

    def __init__(self):
        import torch  # noqa: F401
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from speechbrain.inference.speaker import EncoderClassifier
            self._model = EncoderClassifier.from_hparams(
                source=ECAPA_SOURCE,
                savedir=ECAPA_SAVEDIR,
                run_opts={"device": "cpu"},
            )

    def embed(self, audio: np.ndarray) -> np.ndarray:
        import torch
        wav = torch.from_numpy(np.ascontiguousarray(audio, dtype="float32")).unsqueeze(0)
        with torch.no_grad():
            emb = self._model.encode_batch(wav)  # (1, 1, D)
        return _l2(emb.squeeze().cpu().numpy())


# Cosine threshold for "same speaker". Defaulted to 0.40: clean ECAPA same-speaker
# pairs score ~0.6-0.8, but short/quiet utterances fall lower (observed 0.50 on our
# test clip), while distinct voices sit near ~0.0-0.3. 0.40 groups same-speaker
# reliably with headroom from non-matches; exposed as a flag for live tuning.
DEFAULT_THRESHOLD = 0.40


class OnlineSpeakerClustering:
    """Assign stable Speaker N labels by nearest-centroid cosine matching."""

    def __init__(self, threshold: float = DEFAULT_THRESHOLD):
        self.threshold = threshold
        self._centroids: list[np.ndarray] = []
        self._counts: list[int] = []

    def assign(self, embedding: np.ndarray) -> str:
        if self._centroids:
            sims = [float(np.dot(embedding, c)) for c in self._centroids]
            best = int(np.argmax(sims))
            if sims[best] >= self.threshold:
                # Update running centroid (mean of unit vectors, renormalised).
                n = self._counts[best]
                self._centroids[best] = _l2((self._centroids[best] * n + embedding) / (n + 1))
                self._counts[best] = n + 1
                return f"Speaker {best + 1}"
        self._centroids.append(embedding.copy())
        self._counts.append(1)
        return f"Speaker {len(self._centroids)}"


class SpeakerIdentifier:
    """Top-level: 'Me' for the mic, clustered 'Speaker N' for system audio."""

    def __init__(self, threshold: float = DEFAULT_THRESHOLD):
        self._embedder = EcapaEmbedder()
        self._clustering = OnlineSpeakerClustering(threshold=threshold)

    def identify(self, source_tag: str, audio: np.ndarray) -> str:
        if source_tag == config.SOURCE_ME:
            return "Me"
        return self._clustering.assign(self._embedder.embed(audio))

    def warmup(self) -> None:
        self._embedder.embed(np.zeros(config.SAMPLE_RATE, dtype="float32"))
