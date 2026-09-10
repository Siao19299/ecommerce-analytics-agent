"""Five explicitly tokenized texts: count vectors, NOT model embeddings.

Run after the lexical baseline. This teaching experiment is independent of the
production retrieval scoring and makes no external calls.
"""

import json
import math
from collections import Counter
from pathlib import Path
from typing import Sequence


TEXTS = (
    "商品 金额",
    "支付 金额",
    "支付 支付 金额",
    "支付 金额 支付 金额",
    "实付 钱款",
)
VOCABULARY = ("商品", "支付", "金额", "实付", "钱款")


def count_vectors(texts: Sequence[str], vocabulary: Sequence[str]) -> tuple[tuple[int, ...], ...]:
    """Count space-separated terms in one fixed coordinate system.

    Reject unknown terms instead of silently creating empty or truncated vectors.
    Segmentation is supplied explicitly for this experiment, not learned.
    """
    if not vocabulary or len(set(vocabulary)) != len(vocabulary):
        raise ValueError("Vocabulary must be nonempty and unique")
    vectors = []
    for text in texts:
        counts = Counter(text.split())
        if not set(counts) <= set(vocabulary):
            raise ValueError("Text contains a term outside the vocabulary")
        vectors.append(tuple(counts[term] for term in vocabulary))
    return tuple(vectors)


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine is undefined for zero vectors; fail explicitly rather than use 0."""
    if not left or len(left) != len(right):
        raise ValueError("Vectors must have equal, nonzero dimensions")
    normalized = []
    for vector in (left, right):
        if not all(math.isfinite(value) for value in vector):
            raise ValueError("Vector values must be finite")
        scale = max(abs(value) for value in vector)
        if scale == 0:
            raise ValueError("Cosine is undefined for zero vectors")
        scaled = tuple(value / scale for value in vector)
        norm = math.hypot(*scaled)
        normalized.append(tuple(value / norm for value in scaled))
    result = math.fsum(a * b for a, b in zip(*normalized))
    return max(-1.0, min(1.0, result))


def build_experiment() -> dict:
    vectors = count_vectors(TEXTS, VOCABULARY)
    return {
        "representation": "explicit_space_tokenized_word_counts_not_model_embeddings",
        "vocabulary": VOCABULARY,
        "texts": TEXTS,
        "vectors": vectors,
        "cosine_matrix": [[cosine_similarity(a, b) for b in vectors] for a in vectors],
        "zero_vector_policy": "raise_ValueError_undefined",
        "provenance": "Texts and code prepared by assistant; all scores computed locally.",
        "limitations": [
            "No learned semantics, word order or synonym mapping.",
            "No model tokens, costs, latency or external embedding results are measured.",
            "Does not replace or modify keyword-bigram-idf-v1.",
        ],
    }


def main():
    result = build_experiment()
    root = Path(__file__).parents[2]
    output = root / "data/processed/retrieval/cosine_experiment.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Representation:", result["representation"])
    print("Vocabulary:", result["vocabulary"])
    for index, (text, vector, scores) in enumerate(zip(
        result["texts"], result["vectors"], result["cosine_matrix"],
    ), 1):
        print(f"T{index}", text, vector, "cosines:", [round(score, 6) for score in scores])


if __name__ == "__main__":
    main()
