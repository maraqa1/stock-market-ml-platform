from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, url_for
from markupsafe import Markup, escape

from portal.services.database_reader import db_available
from portal.services.data_quality_service import data_quality_context
from portal.services.gold_service import gold_context
from portal.services.latest_file_reader import count_rows, file_status, latest_file, project_root, readable_reason, safe_read_csv
from portal.services.kpi import trading_cadence_context, trading_header_context, trading_kpi_context
from portal.services.model_validation_service import model_validation_context
from portal.services.signal_service import no_decision_context, signal_context
from portal.services.stock_detail_service import stock_detail_context
from portal.services.trading_api_service import (
    action_queue_context,
    basket_integrity_context,
    basket_today_context,
    monitor_today_context,
    pipeline_current_context,
    pipeline_history_context,
    position_lineage_context,
    positions_context,
)
from portal.services.trading_service import lifecycle_context, position_action, refresh_trading_artifacts, trading_context
from portal.services.universe_service import universe_context
from stockml.services.events import record_event_safely


def create_app(root: Path | None = None) -> Flask:
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
                    if col == "ticker" and value:
                        ticker = escape(str(value).upper())
                        html.append(f'<td><a class="ticker-link" href="{url_for("stock_detail", ticker=ticker)}">{ticker}</a></td>')
                    elif col in {"trade_action", "decision_grade", "sentiment_status"}:
                        badge_value = escape(str(value or "Not available"))
                        html.append(f'<td><span class="badge {badge_value}">{badge_value}</span></td>')
                    else:
                        html.append(f"<td>{escape(str(value if value not in [None, ''] else 'Not available'))}</td>")
                html.append("</tr>")
            html.append("</tbody></table></div>")
            return Markup("".join(html))

        return {"table": table}

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
                {"label": "No Decision", "value": signals["no_decision_count"]},
            ],
            model_status=signals,
            files=[
                *universe["files"],
                file_status(validated, "Price validated universe"),
                file_status(metadata, "Metadata"),
                file_status(feature, "Feature panel"),
                file_status(sentiment, "Sentiment panel"),
                gold["gold_file"],
            ],
        )

    @app.route("/universe")
    def universe():
        return render_template("universe.html", title="Universe", **universe_context(root_path()))

    @app.route("/data-quality")
    def data_quality():
        return render_template("data_quality.html", title="Data Quality", **data_quality_context(root_path()))

    @app.route("/gold")
    def gold():
        return render_template("gold_dataset.html", title="Gold Dataset", **gold_context(root_path()))

    @app.route("/signals")
    def signals():
        return render_template("signals.html", title="Signals", **signal_context(root_path()))

    @app.route("/trading")
    def trading():
        root = root_path()
        context = trading_context(root)
        context.update(
            {
                "trading_header": trading_header_context(root),
                "trading_cadence": trading_cadence_context(root),
                "trading_kpis": trading_kpi_context(root),
                "pipeline_current": pipeline_current_context(root),
                "basket_integrity": basket_integrity_context(root),
                "action_queue": action_queue_context(root),
                "positions_api": positions_context(root),
            }
        )
        return render_template("trading.html", title="Paper Trading", **context)

    @app.route("/trading/_partials/pipeline-strip")
    def trading_pipeline_strip_partial():
        return render_template("trading/_partials/pipeline_strip.html", pipeline_current=pipeline_current_context(root_path()))

    @app.route("/trading/_partials/positions-body")
    def trading_positions_body_partial():
        return render_template("trading/_partials/positions_body.html", positions_api=positions_context(root_path()))

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
        if action == "apply" and decision == "close":
            result = position_action(root_path(), symbol, "close")
        elif action in {"apply", "override"}:
            result = position_action(root_path(), symbol, "keep")
            if action == "override":
                record_event_safely(position_id, "operator_override", "portal_queue", {"event_id": event_id, "symbol": symbol, "decision": decision})
        else:
            result = {"status": "rejected", "message": "unsupported_queue_action", "order_id": ""}
        return jsonify({"status": result.get("status", ""), "event_id": event_id, "symbol": symbol, "broker_order_id": result.get("order_id", ""), "message": result.get("message", ""), "result": result})

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
        return render_template("trading_lifecycle.html", title="Trade Lifecycle", **lifecycle_context(root_path()))

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
        return jsonify(positions_context(root_path()))

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

    @app.route("/model-validation")
    def model_validation():
        return render_template("model_validation.html", title="Model Validation", **model_validation_context(root_path()))

    @app.route("/no-decision")
    def no_decision():
        return render_template("no_decision.html", title="No Decision", **no_decision_context(root_path()))

    @app.route("/stock/<ticker>")
    def stock_detail(ticker: str):
        return render_template("stock_detail.html", title=ticker.upper(), **stock_detail_context(ticker, root_path()))

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8091"))
    app.run(host="0.0.0.0", port=port)
