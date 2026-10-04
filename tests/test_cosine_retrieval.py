import math

import pytest

from src.ecommerce_agent.cosine_retrieval import (
    build_experiment, cosine_similarity, count_vectors,
)


def test_count_representation_preserves_repetitions_and_coordinate_order():
    assert count_vectors(["支付 支付 金额", "商品 金额"], ["商品", "支付", "金额"]) == (
        (0, 2, 1), (1, 0, 1),
    )


def test_cosine_geometric_reference_cases_and_scale_invariance():
    assert cosine_similarity((1, 0), (0, 1)) == 0
    assert cosine_similarity((1, 2), (2, 4)) == pytest.approx(1)
    assert cosine_similarity((1, 0), (-1, 0)) == -1
    assert cosine_similarity((0, 1, 1), (0, 2, 1)) == pytest.approx(3 / math.sqrt(10))
    assert cosine_similarity((1e300, 1e300), (1e-300, 1e-300)) == pytest.approx(1)


@pytest.mark.parametrize("left,right", [
    ([], []), ([1], [1, 2]), ([0, 0], [1, 1]),
    ([float("nan")], [1]), ([float("inf")], [1]),
])
def test_undefined_or_invalid_cosine_is_rejected(left, right):
    with pytest.raises(ValueError):
        cosine_similarity(left, right)


def test_unknown_vocabulary_and_duplicate_coordinates_are_rejected():
    with pytest.raises(ValueError):
        count_vectors(["实付"], ["支付"])
    with pytest.raises(ValueError):
        count_vectors(["支付"], ["支付", "支付"])


def test_five_text_experiment_shows_lexical_semantic_limitation():
    experiment = build_experiment()
    matrix = experiment["cosine_matrix"]
    assert len(matrix) == 5
    for i in range(5):
        assert len(matrix[i]) == 5
        assert matrix[i][i] == pytest.approx(1)
        for j in range(5):
            assert matrix[i][j] == pytest.approx(matrix[j][i])
    assert matrix[1][3] == pytest.approx(1)
    # Similar business wording shares no vocabulary in this explicit count model.
    assert matrix[1][4] == 0
