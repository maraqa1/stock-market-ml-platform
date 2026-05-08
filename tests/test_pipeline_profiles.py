from pathlib import Path

import pytest

from stockml.common.profiles import load_profile, load_profiles


def test_load_pipeline_profiles():
    profiles = load_profiles()
    assert "nasdaq_500" in profiles
    assert profiles["nasdaq_500"]["exchange"] == "NASDAQ"
    assert profiles["nasdaq_500"]["limit_tickers"] == 500


def test_unknown_profile_has_clear_error():
    with pytest.raises(KeyError, match="Unknown profile"):
        load_profile("does_not_exist")

