"""The frozen V1 baseline contract, as executable constants (Phase 2M.2 → 2N).

Every number here is a FROZEN value from
`docs/2026-09-08_V1_DATASET_SCHEMA.md`. The builder and the verifier both
import this module, so the dataset and the thing that checks it cannot drift
apart by editing one and forgetting the other.
"""
from __future__ import annotations

DATASET_VERSION = "V1_BASELINE"
CONTRACT_VERSION = "schema-1.1 / catalog-1.1 (Phase 2M.2)"

CANONICAL_CONVENTION = "canonical_0345"      # 03:45 UTC == 09:15 IST session open
MARKET_PROXY_SYMBOL = "NIFTYBEES.NS"
MARKET_PROXY_NAME = "NIFTYBEES_MARKET_PROXY"

EXPECTED_EQUITY_SYMBOLS = 2468
EXPECTED_CALENDAR_SESSIONS = 2478

WARMUP_SESSIONS = 252                        # 52-week high/low, the longest lookback
MIN_SESSIONS_PER_SYMBOL = WARMUP_SESSIONS + 1
EXTREME_RETURN_THRESHOLD = 0.50

# A date joins the session calendar when it carries at least this share of that
# year's peak equity-symbol count. 167 of the 170 rejected dates carry 1-5
# symbols; the three exceptions are the Muhurat coverage holes named in the
# schema and are documented, not silently dropped.
CALENDAR_MIN_COVERAGE = 0.10
KNOWN_CALENDAR_COVERAGE_HOLES = ("2016-10-30", "2017-10-19", "2018-11-07")

PRICE_FEATURES = [
    "return_1d", "return_5d", "return_21d", "return_63d",
    "sma_20", "sma_50", "sma_200", "ema_20", "ema_50", "ema_200",
    "close_vs_sma20", "close_vs_sma50", "close_vs_sma200", "ema_stack_state",
    "atr_14", "realised_vol_21d", "realised_vol_63d",
    "range_pct", "body_pct", "upper_wick_pct", "lower_wick_pct", "gap_pct",
    "high_20d", "low_20d", "high_52w", "low_52w",
    "range_position_20d", "range_position_52w", "dist_from_52w_high",
    "breakout_20d", "breakdown_20d", "consecutive_up_days",
]
VOLUME_FEATURES = [
    "volume", "avg_volume_20d", "volume_ratio_20d", "volume_accel",
    "turnover", "avg_turnover_20d", "zero_volume_flag",
]
MARKET_FEATURES = [
    "mkt_return_1d", "mkt_return_5d", "mkt_return_21d", "mkt_vol_21d",
    "mkt_above_sma50", "mkt_above_sma200", "rel_strength_21d", "beta_63d",
]
FEATURES = PRICE_FEATURES + VOLUME_FEATURES + MARKET_FEATURES

TARGETS = ["next_session_return", "next_session_high_return", "next_session_low_return"]

KEY_COLUMNS = ["symbol", "prediction_session", "target_session", "calendar_gap_days"]
CONTEXT_COLUMNS = ["close_D", "extreme_return_flag", "locked_bar_flag"]
COLUMNS = KEY_COLUMNS + CONTEXT_COLUMNS + TARGETS + FEATURES

# The only columns permitted to be empty, and only on a locked-circuit bar
# (high == low), where they divide by zero. Documented in the catalog; never
# filled, because a fabricated 0.0 would read as "no wick" rather than
# "no range".
NULLABLE_ON_LOCKED_BAR = ("range_pct", "body_pct", "upper_wick_pct", "lower_wick_pct")

EXCLUSION_REASONS = (
    "WARMUP",                  # fewer than 252 canonical sessions before/at D
    "NOT_NEXT_SESSION",        # session-index distance != 1
    "SESSION_OFF_CALENDAR",    # an endpoint is not an NSE session
    "NON_CANONICAL_BAR",       # bar resolved from an 18:30 / 00:00 legacy row
    "INVALID_BAR",             # non-positive or non-finite OHLC
    "NO_MARKET_PROXY",         # the proxy has no bar for that session
    "INCOMPLETE_FEATURES",     # a required feature is undefined at D
    "EXTREME_RETURN",          # quarantined, retained in the anomaly file
)
