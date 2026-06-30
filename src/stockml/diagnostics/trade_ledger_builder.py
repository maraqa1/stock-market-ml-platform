from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd
from sqlalchemy import asc, select
from sqlalchemy.engine import Engine

from stockml.common.paths import TRADING_DIR, timestamp
from stockml.db.connection import get_engine
from stockml.db.schema import position_events
from stockml.trading.activity_journal_export import ActivityJournalExportRequest, parse_utc_datetime, request_for_date, request_for_range
from stockml.trading.lifecycle_ids import lifecycle_position_id_for, trade_id_for

LEDGER_COLUMNS = ["trade_id","position_id","symbol","side","order_intent","strategy_mode","event_session_mode","planned_execution_session_mode","actual_submission_session_mode","candidate_source","model_version","signal_id","candidate_id","client_order_id","entry_broker_order_id","exit_broker_order_id","entry_time","entry_price","entry_quantity","exit_time","exit_price","exit_quantity","current_price","position_status","realised_pnl","unrealised_pnl","realised_return_pct","unrealised_return_pct","holding_minutes","exit_decision_id","exit_reason","model_score","rank_overall","predicted_rank_pct","meta_label_probability","meta_label_decision","expected_trade_return","risk_adjusted_score","spread_bps_at_entry","spread_bps_at_exit","estimated_slippage_bps","estimated_total_cost","lineage_quality","lineage_warnings"]
UNMATCHED_COLUMNS = ["event_id","event_at","symbol","event_type","source","reason_unmatched","available_trade_id","available_position_id","available_broker_order_id","available_client_order_id","available_candidate_id","suggested_fix"]
SUMMARY_KEYS = ["total_events_scanned","submitted_orders","filled_opening_orders","filled_closing_orders","trades_built","open_trades","closed_trades","cancelled_orders","unmatched_fills","unmatched_closes","unmatched_monitor_events","low_confidence_trades","insufficient_data_trades","lineage_coverage_pct","fit_for_attribution_decision"]
SUBMITTED_EVENTS = {"submitted", "order_submitted", "candidate_submitted"}
FILL_EVENTS = {"filled", "close_filled"}
MONITOR_EVENTS = {"monitor_safe", "monitor_watch", "monitor_rotate", "monitor_close"}
CANCEL_EVENTS = {"cancelled", "canceled", "order_cancelled", "order_canceled"}
CLOSE_INTENTS = {"close_long", "reduce_long", "cover_short", "reduce_short", "manual_close"}
MODEL_FIELDS = ["model_score","rank_overall","predicted_rank_pct","meta_label_probability","meta_label_decision","expected_trade_return","risk_adjusted_score"]

@dataclass(frozen=True)
class TradeLedgerResult:
    ledger: pd.DataFrame
    unmatched: pd.DataFrame
    summary: dict[str, Any]
    ledger_path: Path | None = None
    unmatched_path: Path | None = None
    summary_path: Path | None = None

def _text(v: Any) -> str:
    if v is None: return ""
    s = str(v).strip()
    return "" if s.lower() in {"", "nan", "none", "null", "<na>"} else s

def _num(v: Any) -> float | None:
    s = _text(v)
    if not s: return None
    try: return float(s.replace(",", ""))
    except Exception: return None

def _dt(v: Any) -> datetime | None:
    if isinstance(v, datetime): return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s = _text(v)
    if not s: return None
    try:
        out = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return out if out.tzinfo else out.replace(tzinfo=timezone.utc)
    except Exception: return None

def _iso(v: Any) -> str:
    d = _dt(v)
    return d.isoformat() if d else _text(v)

def _details(e: Mapping[str, Any]) -> dict[str, Any]:
    return e.get("details") if isinstance(e.get("details"), dict) else {}

def _v(e: Mapping[str, Any], k: str) -> Any:
    d = _details(e); v = e.get(k)
    if v in (None, ""): v = d.get(k)
    if k == "broker_order_id" and v in (None, ""): v = d.get("order_id")
    if k == "client_order_id" and v in (None, ""):
        order = d.get("order") if isinstance(d.get("order"), dict) else {}
        v = order.get("client_order_id")
    return v

def _etype(e): return _text(e.get("event_type")).lower()
def _symbol(e):
    s = _text(_v(e,"symbol") or _v(e,"ticker")).upper()
    if not s:
        p = _text(_v(e,"position_id"))
        if ":" in p: s = p.rsplit(":",1)[-1].upper()
    return s

def _side(e):
    intent = _text(_v(e,"order_intent")).lower()
    if intent in {"open_short","cover_short","reduce_short"}: return "short"
    if intent in {"open_long","close_long","reduce_long"}: return "long"
    side = _text(_v(e,"side")).lower()
    if side in {"sell","short"}: return "short"
    if side in {"buy","long"}: return "long"
    action = _text(_v(e,"trade_action") or _v(e,"nightly_bias")).lower()
    if "short" in action: return "short"
    if "long" in action: return "long"
    return ""

def _is_cancel(e):
    status = _text(_v(e,"status") or _v(e,"alpaca_status")).lower()
    return _etype(e) in CANCEL_EVENTS or status in {"cancelled","canceled"}

def _is_close_fill(e):
    return _etype(e) == "close_filled" or _text(_v(e,"order_intent")).lower() in CLOSE_INTENTS or _text(_v(e,"open_or_close")).lower() == "close"

def _is_open_fill(e): return _etype(e) in FILL_EVENTS and not _is_cancel(e) and not _is_close_fill(e)

def _price(e, current=False):
    keys = ["current_price","last_price","close"] if current else ["filled_avg_price","avg_price","entry_price","price","limit_price"]
    for k in keys:
        n = _num(_v(e,k))
        if n is not None and n > 0: return n
    return None

def _qty(e):
    for k in ("filled_qty","qty","quantity","suggested_quantity","entry_quantity"):
        n = _num(_v(e,k))
        if n is not None and abs(n) > 0: return abs(n)
    return None

def _links(e):
    sym = _symbol(e); broker = _text(_v(e,"broker_order_id") or _v(e,"order_id")); client = _text(_v(e,"client_order_id"))
    pos = _text(_v(e,"position_id")); trade = _text(_v(e,"trade_id"))
    if pos.lower().startswith("paper:") and broker: pos = lifecycle_position_id_for(symbol=sym, broker_order_id=broker, client_order_id=client) or pos
    if not trade and _etype(e) in FILL_EVENTS and broker: trade = trade_id_for(symbol=sym, broker_order_id=broker, client_order_id=client) or ""
    return {"trade_id":trade,"position_id":pos,"broker_order_id":broker,"client_order_id":client,"candidate_id":_text(_v(e,"candidate_id")),"signal_id":_text(_v(e,"signal_id"))}

def _unmatched(e, reason, fix):
    l = _links(e)
    return {"event_id":e.get("id",""),"event_at":_iso(e.get("event_at")),"symbol":_symbol(e),"event_type":_etype(e),"source":_text(e.get("source")),"reason_unmatched":reason,"available_trade_id":l["trade_id"],"available_position_id":l["position_id"],"available_broker_order_id":l["broker_order_id"],"available_client_order_id":l["client_order_id"],"available_candidate_id":l["candidate_id"],"suggested_fix":fix}

def _context_key(e):
    l = _links(e)
    for f in ("candidate_id","signal_id","client_order_id","broker_order_id","position_id","trade_id"):
        if l[f]: return f"{f}:{l[f]}"
    sym, cyc = _symbol(e), _text(_v(e,"cycle_id"))
    return f"symbol_cycle:{sym}:{cyc}" if sym and cyc else ""

def _merge_ctx(ctx, e):
    key = _context_key(e)
    if not key: return
    t = ctx.setdefault(key,{})
    for f in ["strategy_mode","event_session_mode","planned_execution_session_mode","actual_submission_session_mode","candidate_source","model_version","signal_id","candidate_id","client_order_id","spread_bps","estimated_slippage_bps","estimated_total_cost",*MODEL_FIELDS]:
        val = _v(e,f)
        if _text(val) and not _text(t.get(f)): t[f] = val

def _lookup_ctx(ctx, e):
    out = {}; l = _links(e); keys=[]
    for f in ("candidate_id","signal_id","client_order_id","broker_order_id","position_id","trade_id"):
        if l[f]: keys.append(f"{f}:{l[f]}")
    sym, cyc = _symbol(e), _text(_v(e,"cycle_id"))
    if sym and cyc: keys.append(f"symbol_cycle:{sym}:{cyc}")
    for k in keys:
        for f,v in ctx.get(k,{}).items():
            if _text(v) and not _text(out.get(f)): out[f]=v
    return out

def _empty(): return {c:"" for c in LEDGER_COLUMNS}

def _open_row(e, ctx):
    l, c, row = _links(e), _lookup_ctx(ctx,e), _empty(); sym=_symbol(e); broker=l["broker_order_id"]; client=l["client_order_id"]
    row.update({"trade_id":l["trade_id"] or trade_id_for(symbol=sym, broker_order_id=broker, client_order_id=client) or "","position_id":l["position_id"] or lifecycle_position_id_for(symbol=sym, broker_order_id=broker, client_order_id=client) or "","symbol":sym,"side":_side(e),"order_intent":_text(_v(e,"order_intent")),"strategy_mode":_text(_v(e,"strategy_mode") or c.get("strategy_mode")),"event_session_mode":_text(_v(e,"event_session_mode") or c.get("event_session_mode")),"planned_execution_session_mode":_text(_v(e,"planned_execution_session_mode") or c.get("planned_execution_session_mode")),"actual_submission_session_mode":_text(_v(e,"actual_submission_session_mode") or c.get("actual_submission_session_mode")),"candidate_source":_text(_v(e,"candidate_source") or c.get("candidate_source")),"model_version":_text(_v(e,"model_version") or c.get("model_version")),"signal_id":l["signal_id"] or _text(c.get("signal_id")),"candidate_id":l["candidate_id"] or _text(c.get("candidate_id")),"client_order_id":client,"entry_broker_order_id":broker,"entry_time":_iso(e.get("event_at")),"entry_price":_price(e),"entry_quantity":_qty(e),"position_status":"open","spread_bps_at_entry":_num(_v(e,"spread_bps") or c.get("spread_bps")),"estimated_slippage_bps":_num(_v(e,"estimated_slippage_bps") or c.get("estimated_slippage_bps")),"estimated_total_cost":_num(_v(e,"estimated_total_cost") or c.get("estimated_total_cost")),"lineage_quality":"high" if (l["trade_id"] and l["position_id"]) else "medium","lineage_warnings":_text(_v(e,"lineage_warning"))})
    for f in MODEL_FIELDS: row[f] = _v(e,f) or c.get(f,"")
    warnings = [p for p in row["lineage_warnings"].split("|") if p]
    for f,w in (("entry_price","missing_entry_price"),("entry_quantity","missing_entry_quantity"),("side","missing_side")):
        if not row[f]: row["position_status"]="insufficient_data"; warnings.append(w)
    row["lineage_warnings"] = "|".join(dict.fromkeys(warnings))
    return row

def _add_indexes(row, indexes):
    key = _text(row.get("trade_id")) or _text(row.get("position_id")) or _text(row.get("entry_broker_order_id")) or _text(row.get("client_order_id")) or _text(row.get("candidate_id"))
    if not key: return
    for name, field in (("trade_id","trade_id"),("position_id","position_id"),("broker_order_id","entry_broker_order_id"),("client_order_id","client_order_id"),("candidate_id","candidate_id"),("signal_id","signal_id")):
        val = _text(row.get(field))
        if val: indexes.setdefault(name,{})[val]=key
    if _text(row.get("symbol")): indexes.setdefault("symbol",{}).setdefault(row["symbol"],[]).append(key)

def _match(e, indexes):
    l = _links(e)
    for field,name in (("trade_id","trade_id"),("position_id","position_id"),("broker_order_id","broker_order_id"),("client_order_id","client_order_id"),("candidate_id","candidate_id"),("signal_id","signal_id")):
        val = l[field]
        if val and val in indexes.get(name,{}): return indexes[name][val], "high"
    sym = _symbol(e)
    if sym and indexes.get("symbol",{}).get(sym): return indexes["symbol"][sym][-1], "low"
    return "", ""

def _close(row, e, quality):
    price, qty = _price(e), (_qty(e) or row.get("entry_quantity")); warnings=[p for p in _text(row.get("lineage_warnings")).split("|") if p]
    if quality == "low": row["lineage_quality"]="low"; warnings.append("symbol_time_fallback_used")
    if price is not None and qty:
        oldq, oldp = _num(row.get("exit_quantity")) or 0.0, _num(row.get("exit_price")) or 0.0
        total = oldq + float(qty); row["exit_price"] = round(((oldp*oldq)+(price*float(qty)))/total, 6); row["exit_quantity"] = total
    row["exit_time"]=_iso(e.get("event_at")); row["exit_broker_order_id"]=_text(_v(e,"broker_order_id") or _v(e,"order_id")); row["exit_decision_id"]=_text(_v(e,"exit_decision_id")) or row.get("exit_decision_id",""); row["exit_reason"]=_text(_v(e,"exit_reason") or _v(e,"reason") or _v(e,"decision_reason")); row["spread_bps_at_exit"]=_num(_v(e,"spread_bps"))
    if row.get("position_status") != "insufficient_data":
        if price is None: row["position_status"]="insufficient_data"; warnings.append("missing_exit_price")
        elif not row.get("entry_price"): row["position_status"]="insufficient_data"; warnings.append("missing_entry_price")
        else: row["position_status"]="closed"
    row["lineage_warnings"]="|".join(dict.fromkeys(warnings))

def _apply_current(row, prices):
    if row.get("position_status") != "open": return
    cur = prices.get(_text(row.get("symbol"))); warnings=[p for p in _text(row.get("lineage_warnings")).split("|") if p]
    if cur is None: warnings.append("missing_current_price"); row["lineage_warnings"]="|".join(dict.fromkeys(warnings)); return
    row["current_price"] = cur

def _pnl(row):
    side, entry, qty = _text(row.get("side")), _num(row.get("entry_price")), _num(row.get("entry_quantity"))
    if entry is None or qty is None or not side: return
    if row.get("position_status") == "closed":
        px, q = _num(row.get("exit_price")), (_num(row.get("exit_quantity")) or qty)
        if px is None: return
        pnl = (px-entry)*q if side=="long" else (entry-px)*q; row["realised_pnl"]=round(pnl,4); row["realised_return_pct"]=round(pnl/(entry*q)*100,6) if entry*q else ""
    elif row.get("position_status") == "open":
        cur = _num(row.get("current_price"))
        if cur is None: return
        pnl = (cur-entry)*qty if side=="long" else (entry-cur)*qty; row["unrealised_pnl"]=round(pnl,4); row["unrealised_return_pct"]=round(pnl/(entry*qty)*100,6) if entry*qty else ""
    a,b = _dt(row.get("entry_time")), (_dt(row.get("exit_time")) or datetime.now(timezone.utc))
    if a: row["holding_minutes"] = round((b-a).total_seconds()/60,2)

def build_trade_ledger_from_events(events: Iterable[Mapping[str, Any]]) -> TradeLedgerResult:
    ordered = sorted([dict(e) for e in events], key=lambda e: (_dt(e.get("event_at")) or datetime.min.replace(tzinfo=timezone.utc), int(e.get("id") or 0)))
    ctx, prices, trades, unmatched = {}, {}, {}, []
    idx = {"trade_id":{},"position_id":{},"broker_order_id":{},"client_order_id":{},"candidate_id":{},"signal_id":{},"symbol":{}}
    submitted=opens=closes=cancelled=unmatched_fills=unmatched_closes=unmatched_monitor=0
    for e in ordered:
        et=_etype(e)
        if _is_cancel(e): cancelled += 1; continue
        if et in SUBMITTED_EVENTS: submitted += 1; _merge_ctx(ctx,e); continue
        if et not in FILL_EVENTS:
            _merge_ctx(ctx,e); cp = _price(e, current=True)
            if cp is not None and _symbol(e): prices[_symbol(e)] = cp
            if et in MONITOR_EVENTS and not _match(e,idx)[0]: unmatched_monitor += 1; unmatched.append(_unmatched(e,"monitor_without_trade_match","attach trade_id/position_id from opening fill to monitor event"))
            continue
        if _is_open_fill(e):
            opens += 1; row = _open_row(e,ctx); key = row["trade_id"] or row["position_id"] or row["entry_broker_order_id"] or row["client_order_id"] or row["candidate_id"]
            if not key: key=f"{row['symbol']}:{row['entry_time']}"; row["lineage_quality"]="low"; row["lineage_warnings"]="|".join(dict.fromkeys([*row["lineage_warnings"].split("|"),"symbol_time_fallback_used"]))
            trades[key]=row; _add_indexes(row,idx); continue
        closes += 1; key, quality = _match(e,idx)
        if not key or key not in trades: unmatched_fills += 1; unmatched_closes += 1; unmatched.append(_unmatched(e,"orphan_close_fill","link close fill to opening trade_id or position_id")); continue
        _close(trades[key],e,quality)
    for row in trades.values(): _apply_current(row,prices); _pnl(row)
    ledger = pd.DataFrame(list(trades.values()), columns=LEDGER_COLUMNS); un = pd.DataFrame(unmatched, columns=UNMATCHED_COLUMNS)
    return TradeLedgerResult(ledger, un, summarize_ledger(ledger, un, total_events=len(ordered), submitted_orders=submitted, filled_opening_orders=opens, filled_closing_orders=closes, cancelled_orders=cancelled, unmatched_fills=unmatched_fills, unmatched_closes=unmatched_closes, unmatched_monitor_events=unmatched_monitor))

def summarize_ledger(ledger: pd.DataFrame, unmatched: pd.DataFrame, *, total_events=0, submitted_orders=0, filled_opening_orders=0, filled_closing_orders=0, cancelled_orders=0, unmatched_fills=0, unmatched_closes=0, unmatched_monitor_events=0):
    n=len(ledger); open_n=int(ledger["position_status"].eq("open").sum()) if n else 0; closed=int(ledger["position_status"].eq("closed").sum()) if n else 0; low=int(ledger["lineage_quality"].eq("low").sum()) if n else 0; insuff=int(ledger["position_status"].eq("insufficient_data").sum()) if n else 0; linked=int(ledger["lineage_quality"].isin(["high","medium"]).sum()) if n else 0
    valid = bool((ledger["entry_price"].astype(str).str.len() > 0).any() and (ledger["side"].astype(str).str.len() > 0).any()) if n else False
    decision = "NOT_FIT_NO_TRADES" if filled_opening_orders == 0 else "NOT_FIT_FIX_LINEAGE" if n == 0 else "NOT_FIT_INSUFFICIENT_PRICES" if (insuff >= n or not valid) else "PARTIAL_ATTRIBUTION_ONLY" if (closed == 0 and open_n > 0) else "FIT_FOR_ATTRIBUTION"
    return {"total_events_scanned":int(total_events),"submitted_orders":int(submitted_orders),"filled_opening_orders":int(filled_opening_orders),"filled_closing_orders":int(filled_closing_orders),"trades_built":int(n),"open_trades":open_n,"closed_trades":closed,"cancelled_orders":int(cancelled_orders),"unmatched_fills":int(unmatched_fills),"unmatched_closes":int(unmatched_closes),"unmatched_monitor_events":int(unmatched_monitor_events),"low_confidence_trades":low,"insufficient_data_trades":insuff,"lineage_coverage_pct":round((linked/n)*100,2) if n else 0.0,"fit_for_attribution_decision":decision}

def load_activity_events(request: ActivityJournalExportRequest, *, target: Engine | None = None) -> list[dict[str, Any]]:
    engine = target or get_engine(required=True); out=[]
    with engine.connect() as conn:
        rows = conn.execute(select(position_events).where(position_events.c.event_at >= request.start, position_events.c.event_at < request.end).order_by(asc(position_events.c.event_at), asc(position_events.c.id))).mappings().all()
    for raw in rows:
        r=dict(raw); d=r.get("details") if isinstance(r.get("details"),dict) else {}; e={**d,"id":r.get("id"),"event_at":r.get("event_at"),"event_type":r.get("event_type"),"source":r.get("source"),"details":d}
        for f in ("pipeline_run_id","cycle_id","signal_id","candidate_id","client_order_id","broker_order_id","position_id","trade_id","exit_decision_id","order_intent","strategy_mode","session_mode","event_session_mode","planned_execution_session_mode","actual_submission_session_mode","candidate_source","model_version","lineage_warning"):
            if _text(r.get(f)): e[f]=r.get(f)
        out.append(e)
    return out

def build_trade_ledger(request: ActivityJournalExportRequest, *, target: Engine | None = None) -> TradeLedgerResult: return build_trade_ledger_from_events(load_activity_events(request, target=target))

def write_trade_ledger(result: TradeLedgerResult, output_dir: Path | str = TRADING_DIR / "diagnostics") -> TradeLedgerResult:
    out=Path(output_dir); out.mkdir(parents=True, exist_ok=True); stamp=timestamp(); ledger_path=out/f"trade_ledger_{stamp}.csv"; unmatched_path=out/f"unmatched_lifecycle_events_{stamp}.csv"; summary_path=out/f"trade_ledger_summary_{stamp}.md"
    result.ledger.to_csv(ledger_path,index=False); result.unmatched.to_csv(unmatched_path,index=False)
    summary_path.write_text("# Unified Paper Trade Ledger Summary\n\n" + "\n".join(f"- {k}: {result.summary.get(k,'')}" for k in SUMMARY_KEYS) + "\n", encoding="utf-8")
    return TradeLedgerResult(result.ledger,result.unmatched,result.summary,ledger_path,unmatched_path,summary_path)

def request_from_args(*, date_value: str = "", start: str = "", end: str = "") -> ActivityJournalExportRequest:
    if date_value: return request_for_date(date.fromisoformat(date_value))
    if start and end: return request_for_range(parse_utc_datetime(start), parse_utc_datetime(end))
    raise ValueError("provide --date or both --start and --end")
