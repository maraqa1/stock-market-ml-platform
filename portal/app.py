from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import requests
from flask import Flask, Response, abort, jsonify, redirect, render_template, request, send_from_directory, url_for
from markupsafe import Markup, escape

from portal.services.database_reader import db_available
from portal.services.data_estate import data_estate_context
from portal.services.data_quality_service import data_quality_context
from portal.services.gold_service import gold_context
from portal.services.gate_controls import enable_paper_allow_all_override, gate_controls_context, set_gate_control
from portal.services.latest_file_reader import count_rows, file_status, latest_file, project_root, readable_reason, safe_read_csv
from portal.services.near_miss_service import near_miss_context
from portal.services.per_symbol_forecast_service import per_symbol_forecast_context
from portal.services.same_day_view import missed_opportunities_context, record_same_day_operator_decision, same_day_panel_context
from portal.services.rotation_diagnostic_service import held_vs_candidate_context
from portal.services.reports_view import closed_trades_context, closed_trades_csv
from portal.services.trade_ledger_view import trade_ledger_context, trade_ledger_csv
from portal.services.diagnostic_reports_view import diagnostic_reports_context, diagnostic_report_csv
from portal.services.kpi import trading_cadence_context, trading_header_context, trading_kpi_context
from portal.services.journal import filters_from_args, iter_csv as journal_iter_csv, query as journal_query
from portal.services.intraday import decisions_csv as intraday_decisions_csv
from portal.services.intraday import decisions_payload as intraday_decisions_payload
from portal.services.intraday import intraday_context, intraday_filters, kill_switch_context, resume_kill_switch, shadow_track_record
from portal.services.search import search
from portal.services.shortlist import get_for_date as shortlist_get_for_date
from portal.services.signal_service import no_decision_context, signal_context
from portal.services.symbol_detail import get as symbol_detail_get
from portal.services.stock_detail_service import stock_detail_context
from portal.services.action_queue_service import action_queue_context
from portal.services.admin_service import admin_context, run_admin_action
from portal.services.trading_api_service import (
    basket_integrity_context,
    basket_today_context,
    monitor_today_context,
    pipeline_current_context,
    pipeline_history_context,
    intraday_promotion_context,
    position_lineage_context,
    positions_context,
)
from portal.services.trading_service import lifecycle_context, position_action, refresh_trading_artifacts, trading_context
from portal.services.universe_service import universe_context
from portal.services.validation import table_csv as validation_table_csv, validation_context
from stockml.intraday.promotion import latest_evaluation
from stockml.autopilot.same_day_promotion import latest_evaluation as latest_same_day_promotion_evaluation
from stockml.safety.live_disabled import assert_live_disabled
from stockml.services.events import record_event_safely
from stockml.autopilot.rotate import confirm_rotation, override_rotation
from stockml.autopilot.open import set_auto_open_enabled, set_auto_open_limit_values, set_auto_open_max_per_day, set_auto_open_strategy_values, set_per_symbol_forecast_fallback_max_per_day
from stockml.trading.paper_autopilot import action as autopilot_action, context as autopilot_context
from stockml.trading.snapshot_export import snapshot_csv_payload
from stockml.trading.timer_settings import save_timer_settings, timer_settings_context
from stockml.reports.daily import dashboard_report_card, get_or_build_report, report_csv, report_index


def trading_snapshot_csv(root: Path) -> str:
    return snapshot_csv_payload(root, snapshot_at=datetime.now(timezone.utc))


def create_app(root: Path | None = None) -> Flask:
    assert_live_disabled()
    app = Flask(__name__)
    app.config["PROJECT_ROOT"] = project_root(root or os.environ.get("STOCKML_PROJECT_ROOT"))

    @app.template_filter("reason")
    def reason_filter(value):
        return readable_reason(value)

    @app.template_filter("fmt")
    def fmt_filter(value):
        if value is None or value == "":
            return "Not available"
        try:
            number = float(value)
            if abs(number) < 1 and number != 0:
                return f"{number:.2%}"
            if abs(number) >= 1000:
                return f"{number:,.0f}"
            return f"{number:.3f}".rstrip("0").rstrip(".")
        except Exception:
            return str(value)

    def root_path() -> Path:
        return app.config["PROJECT_ROOT"]

    @app.context_processor
    def helpers():
        def table(rows, columns=None):
            rows = rows or []
            if not rows:
                return Markup('<div class="empty small">No data available.</div>')
            if columns is None:
                columns = []
                for row in rows:
                    for key in row.keys():
                        if key not in columns:
                            columns.append(key)
                columns = columns[:14]
            html = ['<div class="table-wrap"><table><thead><tr>']
            html.extend(f"<th>{escape(str(col).replace('_', ' ').title())}</th>" for col in columns)
            html.append("</tr></thead><tbody>")
            for row in rows[:200]:
                html.append("<tr>")
                for col in columns:
                    value = row.get(col, "")
                    if col in {"ticker", "symbol"} and value:
                        ticker = escape(str(value).upper())
                        html.append(f'<td><a class="ticker-link" href="{url_for("symbol_detail", symbol=ticker)}">{ticker}</a></td>')
                    elif col in {"trade_action", "decision_grade", "sentiment_status"}:
                        badge_value = escape(str(value or "Not available"))
                        html.append(f'<td><span class="badge {badge_value}">{badge_value}</span></td>')
                    else:
                        html.append(f"<td>{escape(str(value if value not in [None, ''] else 'Not available'))}</td>")
                html.append("</tr>")
            html.append("</tbody></table></div>")
            return Markup("".join(html))

        return {"table": table}

    @app.context_processor
    def nav_context():
        root = root_path()
        queue = action_queue_context(root)
        pipeline = pipeline_current_context(root)
        diagnostics_alert = "alert" if any(str(stage.get("status") or "").lower() in {"failed", "error"} for stage in pipeline.get("stages", [])) else None
        return {
            "pending_count": int((queue.get("counts") or {}).get("total") or 0),
            "diagnostics_alert": diagnostics_alert,
            "account_label": "PA-12345 · operator@stockml",
        }

    @app.route("/health")
    def health():
        signal_file = latest_file(root_path(), "model_outputs", "advanced_model_signal_table_*.csv", fallback_keys=["portal_outputs"])
        gold_file = latest_file(root_path(), "gold", "06_us_gold_ml_dataset_*.csv")
        return jsonify(
            {
                "status": "ok",
                "project_root": str(root_path()),
                "latest_signal_file": str(signal_file) if signal_file else "",
                "latest_gold_file": str(gold_file) if gold_file else "",
                "database_available": db_available(),
                "latest_alpaca_plan_file": str(latest_file(root_path(), "portal_outputs", "08_alpaca_paper_order_plan_*.csv") or ""),
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }
        )

    @app.route("/api/search")
    def api_search():
        try:
            limit = int(request.args.get("limit", "5"))
        except ValueError:
            limit = 5
        return jsonify(search(request.args.get("q", ""), limit=limit, root=root_path(), scope=request.args.get("scope", "all")))

    @app.route("/api/symbols/<symbol>")
    def api_symbol_detail(symbol: str):
        detail = symbol_detail_get(symbol, root_path())
        if detail is None:
            abort(404)
        return jsonify(detail)

    @app.route("/dev/styleguide")
    def dev_styleguide():
        selected_theme = request.args.get("theme", "dark")
        theme = selected_theme if selected_theme in {"dark", "light"} else "dark"
        return render_template("dev_styleguide.html", title="Styleguide", theme=theme)

    @app.route("/")
    def index():
        root = root_path()
        universe = universe_context(root)
        gold = gold_context(root)
        signals = signal_context(root)
        validated = latest_file(root, "interim", "03_us_price_validated_universe_*.csv")
        metadata = latest_file(root, "interim", "04_us_metadata_enriched_*.csv")
        feature = latest_file(root, "processed", "05_us_feature_panel_*.csv")
        sentiment = latest_file(root, "processed", "05_news_sentiment_panel_*.csv")
        return render_template(
            "index.html",
            title="Dashboard",
            kpis=[
                {"label": "Raw universe", "value": universe["raw_count"]},
                {"label": "Tradable universe", "value": universe["tradable_count"]},
                {"label": "Price validated", "value": count_rows(validated)},
                {"label": "Gold rows", "value": gold["row_count"]},
                {"label": "Gold tickers", "value": gold["ticker_count"]},
                {"label": "Long", "value": signals["long_count"]},
                {"label": "Short", "value": signals["short_count"]},
                {"label": "Neutral", "value": signals["no_decision_count"]},
            ],
            model_status=signals,
            daily_report=dashboard_report_card(),
            files=[
                *universe["files"],
                file_status(validated, "Price validated universe"),
                file_status(metadata, "Metadata"),
                file_status(feature, "Feature panel"),
                file_status(sentiment, "Sentiment panel"),
                gold["gold_file"],
            ],
        )

    def ase_agent_dist() -> Path:
        return Path(os.environ.get("ASE_AGENT_DIST", "/home/massa/ase-agent/frontend/dist"))

    def ase_agent_upstream() -> str:
        return os.environ.get("ASE_AGENT_UPSTREAM", "http://127.0.0.1:8000").rstrip("/")

    def ase_fallback_context() -> dict:
        return {
            "title": "ASE Mobile",
            "market": {
                "status": "Delayed",
                "index": "3,910.59",
                "change": "-0.43%",
                "value": "7.8M JD",
                "volume": "6.2M",
                "trades": "3,184",
                "session": "Regular market",
                "hours": "10:30-12:30 AST",
                "updated": "11 Jun 2026, 13:45 AST",
            },
            "watchlist": [
                {"symbol": "JOPH", "name": "Jordan Phosphate Mines", "price": "15.30", "change": "-2.24%", "tone": "down"},
                {"symbol": "JOEP", "name": "Jordan Electric Power", "price": "3.54", "change": "+0.00%", "tone": "flat"},
                {"symbol": "JOPT", "name": "Jordan Petroleum Refinery", "price": "9.01", "change": "-0.77%", "tone": "down"},
            ],
            "movers": [
                {"symbol": "JOPH", "label": "Phosphate watch", "change": "confirmation above 16.20"},
                {"symbol": "JOEP", "label": "Power watch", "change": "staged buy zone"},
                {"symbol": "JOPT", "label": "Refinery watch", "change": "buy support zone"},
            ],
            "disclosures": [
                {"symbol": "JOPH", "type": "Disclosure", "date": "Delayed"},
                {"symbol": "JOEP", "type": "Disclosure", "date": "Delayed"},
                {"symbol": "JOPT", "type": "Disclosure", "date": "Delayed"},
            ],
        }

    @app.route("/ase/api/<path:api_path>", methods=["GET", "POST", "PATCH", "DELETE"])
    def ase_agent_api_proxy(api_path: str):
        upstream = f"{ase_agent_upstream()}/api/{api_path}"
        try:
            upstream_response = requests.request(
                method=request.method,
                url=upstream,
                params=request.args,
                data=request.get_data(),
                headers={key: value for key, value in request.headers if key.lower() not in {"host", "content-length"}},
                timeout=60,
            )
        except requests.RequestException as exc:
            return jsonify({"detail": f"ASE Agent upstream unavailable: {exc}"}), 502
        excluded = {"content-encoding", "content-length", "connection", "transfer-encoding"}
        headers = [(key, value) for key, value in upstream_response.headers.items() if key.lower() not in excluded]
        return Response(upstream_response.content, status=upstream_response.status_code, headers=headers)

    @app.route("/ase")
    @app.route("/ase/")
    def ase_mobile():
        dist = ase_agent_dist()
        index = dist / "index.html"
        if index.exists():
            html = index.read_text(encoding="utf-8")
            fallback = (
                '<noscript><section aria-label="ASE fallback">'
                '<h1>Amman Stock Exchange</h1>'
                '<h2>ASE Index</h2>'
                '<h2>My Watchlist</h2>'
                '<h2>Market Activity</h2>'
                '<h2>Circulars & Disclosures</h2>'
                '<a href="https://aselive.jo/">ASE Live</a>'
                '</section></noscript>'
            )
            if "ASE Index" not in html:
                html = html.replace("</body>", fallback + "</body>")
            return Response(html, mimetype="text/html")
        return render_template("ase_mobile.html", **ase_fallback_context())

    @app.route("/ase/<path:asset_path>")
    def ase_mobile_asset(asset_path: str):
        dist = ase_agent_dist()
        if (dist / asset_path).is_file():
            return send_from_directory(dist, asset_path)
        if (dist / "index.html").exists():
            return send_from_directory(dist, "index.html")
        return redirect(url_for("ase_mobile"))

    @app.route("/universe")
    def universe():
        return render_template("universe.html", title="Universe", **universe_context(root_path()))

    @app.route("/data-quality")
    def data_quality():
        return render_template("data_quality.html", title="Data Quality", **data_quality_context(root_path()))

    @app.route("/admin")
    def admin():
        return render_template("admin.html", title="Admin", **admin_context(root_path()))

    @app.route("/admin/actions/<action>", methods=["POST"])
    def admin_action(action: str):
        result = run_admin_action(root_path(), action)
        if request.accept_mimetypes.best == "application/json":
            return jsonify(result)
        return redirect(url_for("admin"))

    @app.route("/data")
    @app.route("/gold")
    def data_estate():
        return render_template("data_estate.html", title="Data Estate", **data_estate_context(root_path(), request.args.get("dataset")))

    @app.route("/signals")
    def signals():
        return render_template("signals.html", title="Signals", **signal_context(root_path()))

    @app.route("/trading")
    def trading():
        root = root_path()
        context = trading_context(root)
        pipeline_current = pipeline_current_context(root)
        positions_api = positions_context(root)
        action_queue = action_queue_context(root)
        basket_today = basket_today_context(root)
        context.update(
            {
                "trading_header": trading_header_context(root),
                "trading_cadence": trading_cadence_context(root, pipeline=pipeline_current),
                "trading_kpis": trading_kpi_context(root, positions=positions_api, basket=basket_today, queue=action_queue),
                "pipeline_current": pipeline_current,
                "pipeline_history": pipeline_history_context(root, days=14),
                "basket_integrity": basket_integrity_context(root),
                "monitor_activity": monitor_today_context(root),
                "action_queue": action_queue,
                "positions_api": positions_api,
                "held_vs_candidate": held_vs_candidate_context(root),
                "intraday_promotion": intraday_promotion_context(root),
                "same_day_panel": same_day_panel_context(root),
                "near_miss": near_miss_context(root),
                "per_symbol_forecast": per_symbol_forecast_context(root),
                "timer_settings": timer_settings_context(root),
                "paper_autopilot": autopilot_context(root),
                **gate_controls_context(root),
            }
        )
        return render_template("trading.html", title="Paper Trading", **context)

    @app.route("/trading/snapshot.csv")
    def trading_snapshot_download():
        payload = trading_snapshot_csv(root_path())
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        return Response(
            payload,
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename=trading_snapshot_{stamp}.csv"},
        )

    @app.route("/trading/autopilot/<action>", methods=["POST"])
    def trading_autopilot(action: str):
        if action == "mode":
            state = autopilot_action(f"mode:{request.form.get('mode', '')}", root_path())
        else:
            state = autopilot_action(action, root_path())
        if request.accept_mimetypes.best == "application/json":
            return jsonify({"status": "ok", "autopilot": state})
        return redirect(url_for("trading", _anchor="paper-autopilot"))

    @app.route("/trading/autopilot/feature/auto-open", methods=["POST"])
    def trading_autopilot_auto_open_feature():
        enabled = str(request.form.get("enabled", "")).lower() in {"1", "true", "yes", "on"}
        if request.is_json:
            payload_json = request.get_json(silent=True) or {}
            enabled = str(payload_json.get("enabled", "")).lower() in {"1", "true", "yes", "on"}
        return _set_auto_open_feature(enabled)

    @app.route("/trading/autopilot/feature/auto-open/enable", methods=["POST"])
    def trading_autopilot_auto_open_enable():
        return _set_auto_open_feature(True)

    @app.route("/trading/autopilot/feature/auto-open/disable", methods=["POST"])
    def trading_autopilot_auto_open_disable():
        return _set_auto_open_feature(False)

    def _set_auto_open_feature(enabled: bool):
        config = set_auto_open_enabled(enabled, root=root_path())
        payload = {"status": "ok", "auto_open_enabled": config.open_enabled}
        if request.accept_mimetypes.best == "application/json":
            return jsonify(payload)
        return redirect(url_for("trading", _anchor="paper-autopilot"))

    @app.route("/trading/autopilot/feature/auto-open-cap", methods=["POST"])
    def trading_autopilot_auto_open_cap():
        try:
            max_per_day = int(request.form.get("max_auto_opens_per_day", 3))
        except (TypeError, ValueError):
            max_per_day = 3
        config = set_auto_open_max_per_day(max_per_day, root=root_path())
        payload = {"status": "ok", "auto_open_max_per_day": config.max_auto_opens_per_day}
        if request.accept_mimetypes.best == "application/json":
            return jsonify(payload)
        return redirect(url_for("trading", _anchor="paper-autopilot"))

    @app.route("/trading/autopilot/feature/auto-open-limits", methods=["POST"])
    def trading_autopilot_auto_open_limits():
        fields = [
            "max_auto_opens_per_day",
            "max_positions",
            "flat_account_fallback_max_per_day",
            "near_miss_fallback_max_per_day",
            "per_symbol_forecast_fallback_max_per_day",
            "validation_max_new_orders_per_cycle",
            "validation_max_new_orders_per_day",
            "validation_max_open_positions_total",
        ]
        limits = {}
        for field in fields:
            try:
                limits[field] = int(request.form.get(field, 0))
            except (TypeError, ValueError):
                limits[field] = 0
        config = set_auto_open_limit_values(limits, root=root_path())
        payload = {
            "status": "ok",
            "max_auto_opens_per_day": config.max_auto_opens_per_day,
            "max_positions": config.max_positions,
            "flat_account_fallback_max_per_day": config.flat_account_fallback_max_per_day,
            "near_miss_fallback_max_per_day": config.near_miss_fallback_max_per_day,
            "per_symbol_forecast_fallback_max_per_day": config.per_symbol_forecast_fallback_max_per_day,
            "validation_max_new_orders_per_cycle": config.validation_max_new_orders_per_cycle,
            "validation_max_new_orders_per_day": config.validation_max_new_orders_per_day,
            "validation_max_open_positions_total": config.validation_max_open_positions_total,
        }
        if request.accept_mimetypes.best == "application/json":
            return jsonify(payload)
        return redirect(url_for("trading", _anchor="paper-autopilot"))

    @app.route("/trading/autopilot/feature/per-symbol-forecast-cap", methods=["POST"])
    def trading_autopilot_per_symbol_forecast_cap():
        try:
            max_per_day = int(request.form.get("per_symbol_forecast_fallback_max_per_day", 5))
        except (TypeError, ValueError):
            max_per_day = 5
        config = set_per_symbol_forecast_fallback_max_per_day(max_per_day, root=root_path())
        payload = {"status": "ok", "per_symbol_forecast_fallback_max_per_day": config.per_symbol_forecast_fallback_max_per_day}
        if request.accept_mimetypes.best == "application/json":
            return jsonify(payload)
        return redirect(url_for("trading", _anchor="paper-autopilot"))

    @app.route("/trading/autopilot/feature/strategy-risk", methods=["POST"])
    def trading_autopilot_strategy_risk():
        values = {
            "max_position_loss_pct": request.form.get("max_position_loss_pct", "2"),
            "signal_flip_confirmation_clocks": request.form.get("signal_flip_confirmation_clocks", "2"),
            "stale_unknown_loss_close_pct": request.form.get("stale_unknown_loss_close_pct", "2"),
            "trailing_profit_arm_pct": request.form.get("trailing_profit_arm_pct", "2"),
            "trailing_profit_giveback_pct": request.form.get("trailing_profit_giveback_pct", "1"),
            "basket_drawdown_pause_pct": request.form.get("basket_drawdown_pause_pct", "1.5"),
            "max_long_positions": request.form.get("max_long_positions", "0"),
            "max_short_positions": request.form.get("max_short_positions", "0"),
            "near_miss_entries_with_open_positions": request.form.get("near_miss_entries_with_open_positions", ""),
            "close_automation_mode": request.form.get("close_automation_mode", "mixed"),
        }
        config = set_auto_open_strategy_values(values, root=root_path())
        payload = {
            "status": "ok",
            "max_position_loss_pct": config.max_position_loss_pct,
            "signal_flip_confirmation_clocks": config.signal_flip_confirmation_clocks,
            "stale_unknown_loss_close_pct": config.stale_unknown_loss_close_pct,
            "trailing_profit_arm_pct": config.trailing_profit_arm_pct,
            "trailing_profit_giveback_pct": config.trailing_profit_giveback_pct,
            "basket_drawdown_pause_pct": config.basket_drawdown_pause_pct,
            "max_long_positions": config.max_long_positions,
            "max_short_positions": config.max_short_positions,
            "near_miss_entries_with_open_positions": config.near_miss_entries_with_open_positions,
            "close_automation_mode": config.close_automation_mode,
        }
        if request.accept_mimetypes.best == "application/json":
            return jsonify(payload)
        return redirect(url_for("trading", _anchor="paper-autopilot"))

    @app.route("/trading/gate-controls/<control_id>", methods=["POST"])
    def trading_gate_control(control_id: str):
        enabled = str(request.form.get("enabled", "")).strip().lower() in {"1", "true", "yes", "on"}
        if request.is_json:
            payload_json = request.get_json(silent=True) or {}
            enabled = str(payload_json.get("enabled", "")).strip().lower() in {"1", "true", "yes", "on"}
        try:
            payload = set_gate_control(control_id, enabled, root=root_path())
        except KeyError:
            abort(404)
        if request.accept_mimetypes.best == "application/json":
            return jsonify(payload)
        return redirect(url_for("trading", _anchor="gate-controls"))

    @app.route("/trading/gate-controls/override/allow-all-paper", methods=["POST"])
    def trading_gate_control_allow_all_paper():
        payload = enable_paper_allow_all_override(root=root_path())
        if request.accept_mimetypes.best == "application/json":
            return jsonify(payload)
        return redirect(url_for("trading", _anchor="gate-controls"))

    @app.route("/trading/timer-settings", methods=["POST"])
    def trading_timer_settings():
        save_timer_settings(dict(request.form), root_path())
        return redirect(url_for("trading", _anchor="diagnostics"))

    @app.route("/trading/_partials/pipeline-strip")
    def trading_pipeline_strip_partial():
        return render_template("trading/_partials/pipeline_strip.html", pipeline_current=pipeline_current_context(root_path()))

    @app.route("/trading/_partials/positions-body")
    def trading_positions_body_partial():
        return render_template("trading/_partials/positions_body.html", positions_api=positions_context(root_path()))

    @app.route("/trading/_partials/positions-state")
    def trading_positions_state_partial():
        positions_api = positions_context(root_path())
        return jsonify(
            {
                "body_html": render_template("trading/_partials/positions_body.html", positions_api=positions_api),
                "refreshed_at": positions_api.get("refreshed_at") or "",
                "summary": positions_api.get("summary") or {},
                "basket_return": positions_api.get("basket_return") or 0,
                "red_position_pct": positions_api.get("red_position_pct") or 0,
                "basket_state": positions_api.get("basket_state") or "normal",
                "new_entries_paused": bool(positions_api.get("new_entries_paused")),
                "basket_risk_reason_text": positions_api.get("basket_risk_reason_text") or "",
                "pending_close_order_count": positions_api.get("pending_close_order_count") or 0,
            }
        )

    @app.route("/trading/positions/<path:position_id>/lineage")
    def trading_position_lineage_partial(position_id: str):
        return render_template("trading/_partials/lineage.html", lineage=position_lineage_context(root_path(), position_id))

    @app.route("/api/trading/positions/<path:position_id>/close", methods=["POST"])
    def api_trading_position_close(position_id: str):
        symbol = position_id.split(":", 1)[-1].upper()
        result = position_action(root_path(), symbol, "close")
        return jsonify({"status": result.get("status", ""), "symbol": symbol, "broker_order_id": result.get("order_id", ""), "message": result.get("message", ""), "result": result})

    @app.route("/trading/queue/<event_id>/<action>", methods=["POST"])
    def trading_queue_action(event_id: str, action: str):
        payload = request.get_json(silent=True) or request.form
        symbol = str(payload.get("symbol", "")).upper()
        position_id = str(payload.get("position_id") or f"paper:{symbol}")
        decision = str(payload.get("decision", "")).lower()
        if event_id.startswith("rotation-") and decision == "rotate":
            try:
                rotation_id = int(event_id.split("-", 1)[1])
            except ValueError:
                rotation_id = 0
            if action == "override":
                ok = override_rotation(rotation_id)
                result = {"status": "recorded" if ok else "rejected", "message": "rotation_overridden" if ok else "rotation_not_found", "order_id": ""}
            elif action == "apply":
                result = confirm_rotation(rotation_id)
            else:
                result = {"status": "rejected", "message": "unsupported_rotation_action", "order_id": ""}
        elif action == "apply" and decision == "close":
            result = position_action(root_path(), symbol, "close")
        elif action in {"apply", "override"}:
            result = position_action(root_path(), symbol, "keep")
            if action == "override":
                record_event_safely(position_id, "operator_override", "portal_queue", {"event_id": event_id, "symbol": symbol, "decision": decision})
        else:
            result = {"status": "rejected", "message": "unsupported_queue_action", "order_id": ""}
        return jsonify({"status": result.get("status", ""), "event_id": event_id, "symbol": symbol, "broker_order_id": result.get("order_id", ""), "message": result.get("message", ""), "result": result})

    @app.route("/trading/same-day/<int:candidate_id>/<action>", methods=["POST"])
    def trading_same_day_action(candidate_id: int, action: str):
        if action not in {"confirm", "skip"}:
            abort(404)
        result = record_same_day_operator_decision(candidate_id, action)
        if request.accept_mimetypes.best == "application/json":
            return jsonify(result)
        return redirect(url_for("trading", _anchor="same-day-momentum"))

    @app.route("/trading/refresh", methods=["POST"])
    def trading_refresh():
        refresh_trading_artifacts(root_path())
        return redirect(url_for("trading"))

    @app.route("/trading/refresh-data", methods=["POST"])
    def trading_refresh_data():
        refreshed = refresh_trading_artifacts(root_path())
        return jsonify({"status": "ok", **refreshed})

    @app.route("/trading/positions/<symbol>/<action>", methods=["POST"])
    def trading_position_action(symbol: str, action: str):
        result = position_action(root_path(), symbol, action)
        return redirect(
            url_for(
                "trading",
                action_status=result.get("status", ""),
                action_symbol=symbol.upper(),
                action_message=result.get("message", ""),
            )
        )

    @app.route("/trading/lifecycle")
    def trading_lifecycle():
        return redirect(url_for("journal"), code=301)

    @app.route("/lifecycle")
    def lifecycle_redirect():
        return redirect(url_for("journal"), code=301)

    @app.route("/journal")
    def journal():
        filters = filters_from_args(request.args)
        payload = journal_query(filters, cursor=request.args.get("cursor"), limit=request.args.get("limit", 200), root=root_path())
        return render_template(
            "journal/index.html",
            title="Activity Journal",
            filters=filters,
            journal=payload,
            event_types=[
                "scored",
                "ranked",
                "selected",
                "submitted",
                "filled",
                "partial",
                "monitor_safe",
                "monitor_watch",
                "monitor_close",
                "monitor_rotate",
                "operator_keep",
                "operator_close",
                "operator_override",
                "broker_rejected",
                "guardrail_blocked",
            ],
        )

    @app.route("/api/journal/events")
    def api_journal_events():
        filters = filters_from_args(request.args)
        return jsonify(journal_query(filters, cursor=request.args.get("cursor"), limit=request.args.get("limit", 200), root=root_path()))

    @app.route("/api/journal/events.csv")
    def api_journal_events_csv():
        filters = filters_from_args(request.args)
        return Response(
            journal_iter_csv(filters, root=root_path()),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=activity_journal.csv"},
        )

    @app.route("/shortlist")
    def shortlist():
        root = root_path()
        payload = shortlist_get_for_date(
            root,
            request.args.get("date"),
            {
                "bias": request.args.get("bias", "all"),
                "sector": request.args.get("sector", "all"),
                "in_basket": request.args.get("in_basket", "any"),
            },
        )
        return render_template(
            "shortlist.html",
            title="Model Shortlist",
            shortlist=payload,
            trading_header=trading_header_context(root),
            trading_cadence=trading_cadence_context(root),
        )

    @app.route("/intraday")
    def intraday():
        return render_template("intraday/index.html", title="Intraday", intraday=intraday_context(request.args))

    @app.route("/api/intraday/decisions")
    def api_intraday_decisions():
        return jsonify(intraday_decisions_payload(intraday_filters(request.args)))

    @app.route("/api/intraday/decisions.csv")
    def api_intraday_decisions_csv():
        return Response(
            intraday_decisions_csv(intraday_filters(request.args)),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=intraday_decisions.csv"},
        )

    @app.route("/api/intraday/shadow/track-record")
    def api_intraday_shadow_track_record():
        return jsonify(shadow_track_record())

    @app.route("/api/intraday/shadow/aggregates")
    def api_intraday_shadow_aggregates():
        return jsonify(shadow_track_record().get("summary", {}))

    @app.route("/intraday/promotion")
    def intraday_promotion():
        evaluation = latest_evaluation()
        return render_template("intraday/promotion.html", title="Intraday Promotion", evaluation=evaluation)

    @app.route("/autopilot/same_day_promotion")
    def same_day_promotion():
        return render_template(
            "autopilot/same_day_promotion.html",
            title="Same-Day Autopilot Promotion",
            evaluation=latest_same_day_promotion_evaluation(),
        )

    @app.route("/api/intraday/kill-switches")
    def api_intraday_kill_switches():
        return jsonify(kill_switch_context())

    @app.route("/intraday/kill-switches/<path:switch_name>/resume", methods=["POST"])
    def intraday_kill_switch_resume(switch_name: str):
        resume_kill_switch(
            switch_name,
            request.form.get("operator_id", "operator@stockml"),
            request.form.get("notes", "Manual resume confirmed in operator console"),
        )
        if request.accept_mimetypes.best == "application/json":
            return jsonify({"status": "ok", "switch_name": switch_name})
        return redirect(url_for("intraday", _anchor="kill-switches"))

    @app.route("/diagnostics")
    def diagnostics():
        return redirect(url_for("trading", _anchor="diagnostics"))

    @app.route("/symbols/<symbol>")
    def symbol_detail(symbol: str):
        detail = symbol_detail_get(symbol, root_path())
        if detail is None:
            abort(404)
        return render_template("symbols/detail.html", title=detail["symbol"], detail=detail)

    @app.route("/api/trading/pipeline/current")
    def api_trading_pipeline_current():
        return jsonify(pipeline_current_context(root_path()))

    @app.route("/api/trading/pipeline/history")
    def api_trading_pipeline_history():
        try:
            days = int(request.args.get("days", "14"))
        except ValueError:
            days = 14
        return jsonify(pipeline_history_context(root_path(), days=days))

    @app.route("/api/trading/positions")
    def api_trading_positions():
        payload = positions_context(root_path())
        legacy_keys = {"source", "refreshed_at", "summary", "pending_close_order_count", "eod_state", "eod_banner", "positions"}
        return jsonify({key: payload.get(key) for key in legacy_keys})

    @app.route("/api/trading/positions/<path:position_id>/lineage")
    def api_trading_position_lineage(position_id: str):
        return jsonify(position_lineage_context(root_path(), position_id))

    @app.route("/api/trading/basket/today")
    def api_trading_basket_today():
        return jsonify(basket_today_context(root_path()))

    @app.route("/api/trading/basket/integrity")
    def api_trading_basket_integrity():
        return jsonify(basket_integrity_context(root_path()))

    @app.route("/api/trading/monitor/today")
    def api_trading_monitor_today():
        return jsonify(monitor_today_context(root_path()))

    @app.route("/api/trading/queue")
    def api_trading_queue():
        return jsonify(action_queue_context(root_path()))

    @app.route("/api/trading/held-vs-candidate")
    def api_trading_held_vs_candidate():
        return jsonify(held_vs_candidate_context(root_path()))

    @app.route("/api/trading/intraday-promotion")
    def api_trading_intraday_promotion():
        return jsonify(intraday_promotion_context(root_path()))

    @app.route("/validation")
    @app.route("/model-validation")
    def model_validation():
        context = validation_context(
            root_path(),
            model_version=request.args.get("model_version"),
            from_value=request.args.get("from"),
            to_value=request.args.get("to"),
        )
        return render_template("validation/index.html", title="Model Validation", validation=context)

    @app.route("/validation/export/<section>.csv")
    def validation_export(section: str):
        context = validation_context(
            root_path(),
            model_version=request.args.get("model_version"),
            from_value=request.args.get("from"),
            to_value=request.args.get("to"),
        )

    @app.route("/reports")
    def reports_index():
        return render_template("reports/index.html", title="Reports", reports=report_index())

    @app.route("/reports/closed_trades.csv")
    def reports_closed_trades_csv():
        try:
            days = int(request.args.get("days", "30"))
        except ValueError:
            days = 30
        stream = request.args.get("stream") or None
        return Response(
            closed_trades_csv(root_path(), days=days, stream=stream),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=closed_trades_attribution.csv"},
        )

    @app.route("/reports/closed_trades")
    def reports_closed_trades():
        try:
            days = int(request.args.get("days", "30"))
        except ValueError:
            days = 30
        stream = request.args.get("stream") or None
        return render_template(
            "reports/closed_trades.html",
            title="Closed Trades Attribution",
            closed_trades=closed_trades_context(root_path(), days=days, stream=stream),
        )

    @app.route("/reports/trade_ledger")
    def reports_trade_ledger():
        return render_template(
            "reports/trade_ledger.html",
            title="Trade Ledger Diagnostics",
            ledger=trade_ledger_context(root_path()),
        )

    @app.route("/reports/trade_ledger/<kind>.csv")
    def reports_trade_ledger_csv(kind: str):
        csv_text = trade_ledger_csv(root_path(), kind)
        if csv_text is None:
            abort(404)
        return Response(
            csv_text,
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename=trade_ledger_{kind}.csv"},
        )


    @app.route("/reports/diagnostics")
    def reports_diagnostics():
        return render_template(
            "reports/diagnostics.html",
            title="Attribution Diagnostics",
            diagnostics=diagnostic_reports_context(root_path()),
        )

    @app.route("/reports/diagnostics/<kind>.csv")
    def reports_diagnostics_csv(kind: str):
        csv_text = diagnostic_report_csv(root_path(), kind)
        if csv_text is None:
            abort(404)
        return Response(
            csv_text,
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename=diagnostic_{kind}.csv"},
        )

    @app.route("/reports/daily/<report_date>.json")
    def reports_daily_json(report_date: str):
        return jsonify(get_or_build_report(report_date, refresh=request.args.get("refresh") == "1"))

    @app.route("/reports/daily/<report_date>.csv")
    def reports_daily_csv(report_date: str):
        report = get_or_build_report(report_date, refresh=request.args.get("refresh") == "1")
        return Response(
            report_csv(report),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename=daily_report_{report_date}.csv"},
        )

    @app.route("/reports/daily/<report_date>")
    def reports_daily(report_date: str):
        report = get_or_build_report(report_date, refresh=request.args.get("refresh") == "1")
        return render_template("reports/daily.html", title=f"Daily Report {report_date}", report=report)
        csv_text = validation_table_csv(section, context)
        return Response(
            csv_text,
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename=validation_{section}.csv"},
        )

    @app.route("/reports/missed_opportunities/<report_date>")
    def reports_missed_opportunities(report_date: str):
        return render_template(
            "reports/missed_opportunities.html",
            title=f"Missed Opportunities {report_date}",
            missed=missed_opportunities_context(report_date),
        )

    @app.route("/no-decision")
    def no_decision():
        return render_template("no_decision.html", title="Neutral", **no_decision_context(root_path()))

    @app.route("/stock/<ticker>")
    def stock_detail(ticker: str):
        return render_template("stock_detail.html", title=ticker.upper(), **stock_detail_context(ticker, root_path()))

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8091"))
    app.run(host="0.0.0.0", port=port)
