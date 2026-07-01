from __future__ import annotations

from pathlib import Path

from stockml.candidates.short_side_policy import ShortSidePolicy, load_short_side_policy, short_side_block_reason


def test_short_policy_blocks_short_by_default():
    assert short_side_block_reason({"side": "sell"}, ShortSidePolicy()) == "short_side_validation_required"


def test_short_policy_does_not_block_long():
    assert short_side_block_reason({"side": "buy"}, ShortSidePolicy()) == ""


def test_short_policy_allows_short_when_enabled_and_allowed():
    policy = ShortSidePolicy(enabled=True, allow_shorts_in_validation=True)
    assert short_side_block_reason({"trade_action": "Short"}, policy) == ""


def test_short_policy_loads_config(tmp_path: Path):
    path = tmp_path / "trading.yaml"
    path.write_text(
        "\n".join(
            [
                "short_side_policy:",
                "  enabled: true",
                "  allow_shorts_in_validation: true",
                "  require_short_side_attribution_pass: false",
                "  research_only_when_disabled: false",
            ]
        ),
        encoding="utf-8",
    )
    policy = load_short_side_policy(path)
    assert policy.enabled is True
    assert policy.allow_shorts_in_validation is True
    assert policy.require_short_side_attribution_pass is False
    assert policy.research_only_when_disabled is False

