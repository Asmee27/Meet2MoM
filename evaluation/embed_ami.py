"""Embed every AMI utterance once with the production embedding model (cached .npy per meeting)."""
from __future__ import annotations

import json
import sys
import time

import numpy as np

from evaluation.harness import AMI_DERIVED

EMB_DIR = AMI_DERIVED.parent / "emb"


def main() -> None:
    from app.core.model_registry import get_sentence_transformer
    from app.services.verification.retrieval_index import EMBED_MODEL_NAME

    EMB_DIR.mkdir(parents=True, exist_ok=True)
    model = get_sentence_transformer(EMBED_MODEL_NAME)
    started = time.perf_counter()
    paths = sorted(AMI_DERIVED.glob("*.json"))
    total = 0
    for i, path in enumerate(paths):
        out = EMB_DIR / f"{path.stem}.npy"
        if out.exists():
            continue
        texts = [u["text"] for u in json.loads(path.read_text(encoding="utf-8"))["utterances"]]
        emb = model.encode(texts, batch_size=64, normalize_embeddings=True, show_progress_bar=False)
        np.save(out, np.asarray(emb, dtype=np.float32))
        total += len(texts)
        print(f"{i + 1}/{len(paths)} {path.stem}: {len(texts)} utts, {time.perf_counter() - started:.0f}s", flush=True)
    print(f"done: {total} utterances in {time.perf_counter() - started:.0f}s")


if __name__ == "__main__":
    sys.exit(main())
