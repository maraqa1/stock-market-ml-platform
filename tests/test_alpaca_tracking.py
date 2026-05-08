import math

import pandas as pd

from stockml.trading.paper_trader import _clean_text


def test_clean_text_treats_nan_order_ids_as_blank():
    assert _clean_text(float("nan")) == ""
    assert _clean_text(math.nan) == ""
    assert _clean_text("nan") == ""
    assert _clean_text(None) == ""
    assert _clean_text("abc-123") == "abc-123"


def test_clean_text_handles_pandas_missing_value():
    assert _clean_text(pd.NA) == ""
