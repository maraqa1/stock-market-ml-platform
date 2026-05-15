from __future__ import annotations

from stockml.trading.outcome_reasons import HUMAN_LABELS, OutcomeReason
from stockml.trading.reason_normalizer import normalize_concatenated, normalize_reason


def test_human_labels_cover_every_enum_value():
    assert set(HUMAN_LABELS) == set(OutcomeReason)


def test_casing_normalization():
    assert normalize_reason("Meta label probability below threshold") == OutcomeReason.REJECTED_META_LABEL_THRESHOLD
    assert normalize_reason("meta label probability below threshold") == OutcomeReason.REJECTED_META_LABEL_THRESHOLD


def test_approved_is_stage_verdict_not_terminal_reason():
    reason, verdicts = normalize_concatenated("Approved")

    assert reason is None
    assert verdicts["trade_quality"] == "approved"


def test_concatenated_reason_splits_binding_reason_and_stage_verdicts():
    reason, verdicts = normalize_concatenated("Approved; Meta label probability below threshold")

    assert reason == OutcomeReason.REJECTED_META_LABEL_THRESHOLD
    assert verdicts["trade_quality"] == "approved"
    assert verdicts["meta_label"] == "rejected:below_threshold"


def test_last_non_approved_reason_is_terminal():
    reason, verdicts = normalize_concatenated("Trimmed size; Meta label probability below threshold")

    assert reason == OutcomeReason.REJECTED_META_LABEL_THRESHOLD
    assert verdicts["sizing"] == "trimmed_to_zero"
    assert verdicts["meta_label"] == "rejected:below_threshold"
