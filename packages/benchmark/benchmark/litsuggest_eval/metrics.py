from __future__ import annotations

from dataclasses import dataclass

ALL_LABELS: tuple[str, ...] = ("relevant", "borderline", "not_relevant")

GROUND_TRUTH_TO_PIPELINE: dict[str, str] = {
    "Curated": "relevant",
    "Relevant": "borderline",
    "Relevant Association": "borderline",
    "Not Curated": "not_relevant",
    "Not Relevant": "not_relevant",
}


def map_ground_truth(curation_state: str) -> str:
    """Map a PharmGKB curation state to the pipeline's 3-class taxonomy."""
    return GROUND_TRUTH_TO_PIPELINE[curation_state]


@dataclass(frozen=True)
class LabelMetrics:
    precision: float
    recall: float
    f1: float
    support: int


@dataclass
class EvalResult:
    n_total: int
    accuracy: float
    label_metrics: dict[str, LabelMetrics]
    macro_f1: float
    confusion: dict[str, dict[str, int]]  # confusion[true_label][pred_label] = count


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
    return p, r, f1


def compute_metrics(pairs: list[tuple[str, str]]) -> EvalResult:
    """Compute classification metrics from (predicted_label, true_label) pairs."""
    n = len(pairs)
    if n == 0:
        empty_metrics = {lbl: LabelMetrics(0.0, 0.0, 0.0, 0) for lbl in ALL_LABELS}
        empty_conf = {lbl: {p: 0 for p in ALL_LABELS} for lbl in ALL_LABELS}
        return EvalResult(0, 0.0, empty_metrics, 0.0, empty_conf)

    correct = sum(1 for pred, true in pairs if pred == true)
    accuracy = correct / n

    confusion: dict[str, dict[str, int]] = {
        lbl: {p: 0 for p in ALL_LABELS} for lbl in ALL_LABELS
    }
    for pred, true in pairs:
        true_key = true if true in confusion else "not_relevant"
        pred_key = pred if pred in ALL_LABELS else "not_relevant"
        confusion[true_key][pred_key] += 1

    label_metrics: dict[str, LabelMetrics] = {}
    for label in ALL_LABELS:
        tp = confusion[label][label]
        fp = sum(confusion[t][label] for t in ALL_LABELS if t != label)
        fn = sum(confusion[label][p] for p in ALL_LABELS if p != label)
        p, r, f1 = _prf(tp, fp, fn)
        support = sum(confusion[label].values())
        label_metrics[label] = LabelMetrics(precision=p, recall=r, f1=f1, support=support)

    macro_f1 = sum(m.f1 for m in label_metrics.values()) / len(ALL_LABELS)

    return EvalResult(
        n_total=n,
        accuracy=accuracy,
        label_metrics=label_metrics,
        macro_f1=macro_f1,
        confusion=confusion,
    )
