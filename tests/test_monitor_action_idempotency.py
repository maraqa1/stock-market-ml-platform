import pandas as pd
from sqlalchemy import create_engine, select

from stockml.agents.position_decision_engine import write_position_decisions
from stockml.db.schema import create_all, position_events
from stockml.services import events


def test_monitor_replace_repeated_within_cooldown_is_skipped(monkeypatch, tmp_path):
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    monkeypatch.setattr(events, "get_engine", lambda required=False: engine)
    frame = pd.DataFrame([{"symbol": "AAA", "decision": "replace", "recommended_action": "close_then_open_replacement", "replacement_symbol": "BBB"}])
    monkeypatch.setattr("stockml.agents.position_decision_engine.AGENT_DECISIONS_DIR", tmp_path)
    write_position_decisions(frame, stamp="one")
    write_position_decisions(frame, stamp="two")
    with engine.connect() as conn:
        rows = conn.execute(select(position_events).where(position_events.c.event_type == "monitor_rotate")).all()
    assert len(rows) == 1
