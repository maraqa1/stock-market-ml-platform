# Complete Activity Journal Export

The activity journal page remains paginated for portal responsiveness, but the downloadable export path now streams every matching event for an explicit date or time range.

Use the command-line exporter for diagnostics:

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/export_activity_journal.py \
  --date 2026-06-23 \
  --output data/trading/exports/
```

The exporter also accepts `--start`, `--end`, `--source`, `--event-type`, and `--symbol`.

Each run writes:

- `activity_journal_YYYYMMDD_HHMMSS.csv`
- `activity_journal_YYYYMMDD_HHMMSS.metadata.json`

The metadata records the requested range, row count, first and last event ids, and `was_truncated`. A successful explicit export must report `was_truncated=false`.

This export is read-only and does not alter trading state.
