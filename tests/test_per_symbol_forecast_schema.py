from stockml.trading.per_symbol_forecast.schema import OUTPUT_COLUMNS, TIER_C_FIELDS, output_record


def test_every_documented_field_present():
    record = output_record({"symbol": "AAPL"})

    assert list(record.keys()) == OUTPUT_COLUMNS
    assert record["diagnostic_only"] is True
    assert record["tier_c_status"] == "uncalibrated"


def test_tier_c_fields_default_to_null():
    record = output_record({"symbol": "AAPL"})

    for field in TIER_C_FIELDS:
        assert record[field] is None
