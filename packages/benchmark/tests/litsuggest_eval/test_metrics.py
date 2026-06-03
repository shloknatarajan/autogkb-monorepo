import pytest

from benchmark.litsuggest_eval.metrics import (
    ALL_LABELS,
    GROUND_TRUTH_TO_PIPELINE,
    EvalResult,
    LabelMetrics,
    compute_metrics,
    map_ground_truth,
)


def test_label_mapping_covers_all_ground_truth_states() -> None:
    expected_keys = {"Curated", "Relevant", "Relevant Association", "Not Curated", "Not Relevant"}
    assert set(GROUND_TRUTH_TO_PIPELINE.keys()) == expected_keys


def test_label_mapping_curated_is_relevant() -> None:
    assert map_ground_truth("Curated") == "relevant"


def test_label_mapping_not_relevant_is_not_relevant() -> None:
    assert map_ground_truth("Not Relevant") == "not_relevant"
    assert map_ground_truth("Not Curated") == "not_relevant"


def test_label_mapping_relevant_variants_are_borderline() -> None:
    assert map_ground_truth("Relevant") == "borderline"
    assert map_ground_truth("Relevant Association") == "borderline"


def test_perfect_predictions() -> None:
    pairs = [
        ("relevant", "relevant"),
        ("borderline", "borderline"),
        ("not_relevant", "not_relevant"),
    ]
    result = compute_metrics(pairs)
    assert result.accuracy == pytest.approx(1.0)
    assert result.macro_f1 == pytest.approx(1.0)
    for lbl in ALL_LABELS:
        assert result.label_metrics[lbl].precision == pytest.approx(1.0)
        assert result.label_metrics[lbl].recall == pytest.approx(1.0)
        assert result.label_metrics[lbl].f1 == pytest.approx(1.0)


def test_all_wrong_predictions() -> None:
    pairs = [
        ("not_relevant", "relevant"),
        ("not_relevant", "borderline"),
        ("relevant", "not_relevant"),
    ]
    result = compute_metrics(pairs)
    assert result.accuracy == pytest.approx(0.0)


def test_precision_and_recall_relevant_label() -> None:
    # 2 predicted relevant: 1 correct (TP), 1 wrong (FP)
    # 2 true relevant: 1 caught (TP), 1 missed (FN)
    pairs = [
        ("relevant", "relevant"),    # TP
        ("relevant", "borderline"),  # FP for relevant
        ("borderline", "relevant"),  # FN for relevant
        ("borderline", "borderline"),# TP
    ]
    result = compute_metrics(pairs)
    m = result.label_metrics["relevant"]
    assert m.precision == pytest.approx(0.5)
    assert m.recall == pytest.approx(0.5)
    assert m.f1 == pytest.approx(0.5)


def test_confusion_matrix_shape() -> None:
    pairs = [("relevant", "relevant"), ("borderline", "not_relevant")]
    result = compute_metrics(pairs)
    for lbl in ALL_LABELS:
        assert lbl in result.confusion


def test_support_counts_match_ground_truth() -> None:
    pairs = [
        ("relevant", "relevant"),
        ("borderline", "relevant"),
        ("not_relevant", "not_relevant"),
    ]
    result = compute_metrics(pairs)
    assert result.label_metrics["relevant"].support == 2
    assert result.label_metrics["not_relevant"].support == 1


def test_empty_input_returns_zero_accuracy() -> None:
    result = compute_metrics([])
    assert result.accuracy == pytest.approx(0.0)
    assert result.n_total == 0
