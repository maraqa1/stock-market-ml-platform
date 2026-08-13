from __future__ import annotations

from stockml.trading_brain_v2.autopilot.ap_b01_gold_dataset_intake import GoldDatasetIntakeBlock
from stockml.trading_brain_v2.autopilot.ap_b02_candidate_normalizer import CandidateNormalizerBlock
from stockml.trading_brain_v2.autopilot.ap_b03_candidate_validity_gate import CandidateValidityGateBlock
from stockml.trading_brain_v2.autopilot.ap_b04_ai2_status_interpreter import AI2StatusInterpreterBlock
from stockml.trading_brain_v2.autopilot.ap_b05_warning_interpreter import WarningInterpreterBlock
from stockml.trading_brain_v2.autopilot.ap_b06_refresh_gate import RefreshGateBlock
from stockml.trading_brain_v2.autopilot.ap_b07_tradability_gate import TradabilityGateBlock
from stockml.trading_brain_v2.autopilot.ap_b08_risk_scoring_engine import RiskScoringEngineBlock
from stockml.trading_brain_v2.autopilot.ap_b09_position_sizing_engine import PositionSizingEngineBlock
from stockml.trading_brain_v2.autopilot.ap_b10_entry_decision_engine import EntryDecisionEngineBlock
from stockml.trading_brain_v2.autopilot.ap_b11_trade_intent_builder import TradeIntentBuilderBlock
from stockml.trading_brain_v2.autopilot.ap_b12_execution_handoff import ExecutionHandoffBlock


AUTOPILOT_BLOCKS = (
    GoldDatasetIntakeBlock,
    CandidateNormalizerBlock,
    CandidateValidityGateBlock,
    AI2StatusInterpreterBlock,
    WarningInterpreterBlock,
    RefreshGateBlock,
    TradabilityGateBlock,
    RiskScoringEngineBlock,
    PositionSizingEngineBlock,
    EntryDecisionEngineBlock,
    TradeIntentBuilderBlock,
    ExecutionHandoffBlock,
)

