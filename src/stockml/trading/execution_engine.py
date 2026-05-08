from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from stockml.trading.alpaca_client import AlpacaAPIError, AlpacaPaperClient
from stockml.trading.config import AlpacaConfig, alpaca_config
from stockml.trading.order_builder import bracket_order_payload, validate_order_payload
from stockml.trading.risk_manager import ExecutionRiskPolicy, RiskManager


def _optional_sdk_client(config: AlpacaConfig):
    try:
        from alpaca.trading.client import TradingClient  # type: ignore
    except Exception:
        return None
    return TradingClient(config.api_key, config.secret_key, paper=True)


class AlpacaExecutionEngine:
    def __init__(
        self,
        config: AlpacaConfig | None = None,
        mode: str = "dry_run",
        client: Any | None = None,
        risk_manager: RiskManager | None = None,
        use_sdk: bool = True,
    ) -> None:
        self.config = config or alpaca_config()
        self.mode = mode
        if self.mode == "live" and not self.config.live_trading_enabled:
            raise RuntimeError("Live trading is disabled. Set ALLOW_LIVE_TRADING only after explicit production approval.")
        self.client = client or (_optional_sdk_client(self.config) if use_sdk else None) or AlpacaPaperClient(self.config)
        self.risk_manager = risk_manager or RiskManager(
            ExecutionRiskPolicy(
                max_single_position_exposure=self.config.max_notional_per_order,
                max_order_notional=self.config.max_notional_per_order,
                max_portfolio_exposure=self.config.max_total_notional,
                max_daily_trades=self.config.max_orders,
                min_confidence=self.config.min_side_probability,
                min_avg_dollar_volume=self.config.min_avg_dollar_volume_20d,
                allow_short_selling=self.config.allow_short_selling,
            )
        )

    def execute(self, recommendations: pd.DataFrame | list[dict[str, Any]]) -> pd.DataFrame:
        frame = pd.DataFrame(recommendations)
        rows = []
        submitted_count = 0
        for rec in frame.to_dict("records"):
            row = self._process_recommendation(rec, submitted_count)
            if row["decision"] in {"submitted", "dry_run"}:
                submitted_count += 1
            rows.append(row)
        return pd.DataFrame(rows)

    def _process_recommendation(self, rec: dict[str, Any], submitted_count: int) -> dict[str, Any]:
        symbol = str(rec.get("symbol") or rec.get("ticker") or "").upper()
        signal = str(rec.get("signal") or rec.get("trade_action") or "").lower()
        side = "buy" if signal == "long" else "sell"
        ok, reason = self.risk_manager.validate_recommendation(rec, daily_count=submitted_count)
        if not ok:
            return self._report(rec, "skipped" if reason in {"hold_or_no_decision", "confidence_below_threshold"} else "rejected", reason)
        notional = self.risk_manager.approved_notional(rec)
        last_price = float(rec.get("last_price") or rec.get("current_price") or rec.get("close") or 0)
        qty = int(notional // last_price) if last_price > 0 else 0
        if qty < 1:
            return self._report(rec, "rejected", "quantity_below_one")
        stop_loss_pct = float(rec.get("stop_loss_pct") or 0.03)
        take_profit_pct = float(rec.get("take_profit_pct") or 0.06)
        if side == "buy":
            take_profit = last_price * (1 + take_profit_pct)
            stop_loss = last_price * (1 - stop_loss_pct)
        else:
            take_profit = last_price * (1 - take_profit_pct)
            stop_loss = last_price * (1 + stop_loss_pct)
        payload = bracket_order_payload(symbol, side, qty, "market", "day", take_profit, stop_loss, f"stockml-{datetime.now().strftime('%Y%m%d%H%M%S')}-{symbol}-{side}")
        validation = validate_order_payload(payload, max_order_notional=self.config.max_notional_per_order)
        if not validation.valid:
            return self._report(rec, "rejected", validation.reason, payload=payload)
        if self.mode == "dry_run":
            return self._report(rec, "dry_run", "validated_dry_run", payload=payload, qty=qty, take_profit=take_profit, stop_loss=stop_loss)
        if self.mode != "paper":
            return self._report(rec, "rejected", "unsupported_execution_mode", payload=payload)
        try:
            response = self._submit_payload(payload)
            return self._report(rec, "submitted", "submitted", payload=payload, response=response, qty=qty, take_profit=take_profit, stop_loss=stop_loss)
        except AlpacaAPIError as exc:
            return self._report(rec, "rejected", "alpaca_api_error", payload=payload, diagnostics=exc.as_dict(), qty=qty, take_profit=take_profit, stop_loss=stop_loss)
        except Exception as exc:
            return self._report(rec, "rejected", f"submit_exception: {exc}", payload=payload, qty=qty, take_profit=take_profit, stop_loss=stop_loss)

    def _submit_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        if isinstance(self.client, AlpacaPaperClient):
            return self.client.submit_order(payload)
        # SDK fallback: use raw dict if a fake test client supports it; real SDK users can adapt request object support here.
        if hasattr(self.client, "submit_order"):
            response = self.client.submit_order(payload)
            if isinstance(response, dict):
                return response
            return response.model_dump() if hasattr(response, "model_dump") else dict(response)
        raise RuntimeError("client does not support submit_order")

    def _report(
        self,
        rec: dict[str, Any],
        decision: str,
        reason: str,
        payload: dict[str, Any] | None = None,
        response: dict[str, Any] | None = None,
        diagnostics: dict[str, Any] | None = None,
        qty: int = 0,
        take_profit: float | None = None,
        stop_loss: float | None = None,
    ) -> dict[str, Any]:
        payload = payload or {}
        response = response or {}
        diagnostics = diagnostics or {}
        return {
            "symbol": str(rec.get("symbol") or rec.get("ticker") or "").upper(),
            "signal": rec.get("signal", rec.get("trade_action", "")),
            "decision": decision,
            "reason": reason,
            "order_id": response.get("id", ""),
            "client_order_id": payload.get("client_order_id", ""),
            "submitted_qty": qty or payload.get("qty", ""),
            "submitted_notional": payload.get("notional", ""),
            "entry_type": payload.get("type", ""),
            "take_profit": round(float(take_profit), 4) if take_profit is not None else "",
            "stop_loss": round(float(stop_loss), 4) if stop_loss is not None else "",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "payload": str(payload),
            "alpaca_response": str(response),
            "http_status": diagnostics.get("http_status", ""),
            "request_id": diagnostics.get("request_id", ""),
            "api_error": diagnostics.get("api_error", ""),
        }
