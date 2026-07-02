# Short Side Validation Policy

Short execution is disabled by default in validation mode. Short candidates remain visible for research and inverse-shadow diagnostics, but they are not eligible for Paper Autopilot or basket submission unless short-side attribution passes explicit enablement thresholds.

Enablement requires enough closed short trades, positive realised edge, acceptable win rate, and profit factor above the configured threshold. Reconstructed attribution is still reported, but the diagnostics mark it as lower-confidence evidence.
