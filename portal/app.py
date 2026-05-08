from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template, url_for
from markupsafe import Markup, escape

from portal.services.database_reader import db_available
from portal.services.data_quality_service import data_quality_context
from portal.services.gold_service import gold_context
from portal.services.latest_file_reader import count_rows, file_status, latest_file, project_root, readable_reason, safe_read_csv
from portal.services.model_validation_service import model_validation_context
from portal.services.signal_service import no_decision_context, signal_context
from portal.services.stock_detail_service import stock_detail_context
from portal.services.universe_service import universe_context


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
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }
        )

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
