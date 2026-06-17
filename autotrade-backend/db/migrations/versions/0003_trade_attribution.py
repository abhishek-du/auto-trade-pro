"""Add trade attribution columns to paper_trades + trade_excursion_samples table.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-17

All new columns are nullable so existing rows and all existing code paths
continue to work unchanged.  New code paths populate them going forward.
"""
from alembic import op
import sqlalchemy as sa

revision      = "0003"
down_revision = "0002"
branch_labels = None
depends_on    = None

_ATTRIBUTION_COLS = [
    # ── Entry snapshot ────────────────────────────────────────────────────────
    ("strategy_name",      sa.String(40)),
    ("regime_at_entry",    sa.String(20)),
    ("entry_reason",       sa.String(40)),
    ("confidence_bucket",  sa.String(8)),
    ("instrument_segment", sa.String(12)),
    ("initial_risk_inr",   sa.Float()),
    # ── Exit snapshot ─────────────────────────────────────────────────────────
    ("exit_reason",        sa.String(20)),
    ("regime_at_exit",     sa.String(20)),
    ("r_multiple",         sa.Float()),
    ("holding_bars",       sa.Integer()),
    ("holding_hours",      sa.Float()),
    # ── Excursion summary (populated at close from running peak/trough) ───────
    ("mfe_abs",            sa.Float()),
    ("mfe_pct",            sa.Float()),
    ("mfe_r",              sa.Float()),
    ("mae_abs",            sa.Float()),
    ("mae_pct",            sa.Float()),
    ("mae_r",              sa.Float()),
    ("max_open_profit",    sa.Float()),
]


def upgrade() -> None:
    # ── 1. New columns on paper_trades (all nullable) ─────────────────────────
    for col_name, col_type in _ATTRIBUTION_COLS:
        op.add_column(
            "paper_trades",
            sa.Column(col_name, col_type, nullable=True),
        )

    # ── 2. Indexes for common filter / GROUP-BY patterns ──────────────────────
    op.create_index("ix_pt_strategy_name",  "paper_trades", ["strategy_name"])
    op.create_index("ix_pt_regime_entry",   "paper_trades", ["regime_at_entry"])
    op.create_index("ix_pt_conf_bucket",    "paper_trades", ["confidence_bucket"])
    op.create_index("ix_pt_instrument_seg", "paper_trades", ["instrument_segment"])
    op.create_index("ix_pt_exit_reason",    "paper_trades", ["exit_reason"])

    # ── 3. trade_excursion_samples — append-only per-tick excursion data ──────
    op.create_table(
        "trade_excursion_samples",
        sa.Column("id",             sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("trade_id",       sa.Integer(),
                  sa.ForeignKey("paper_trades.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("ts",             sa.DateTime(),  nullable=False),
        sa.Column("price",          sa.Float(),     nullable=False),
        sa.Column("unrealised_pnl", sa.Float(),     nullable=False),
        sa.Column("unrealised_r",   sa.Float(),     nullable=True),
    )
    op.create_index(
        "ix_excursion_trade_ts", "trade_excursion_samples", ["trade_id", "ts"]
    )


def downgrade() -> None:
    op.drop_index("ix_excursion_trade_ts",  "trade_excursion_samples")
    op.drop_table("trade_excursion_samples")

    for idx in [
        "ix_pt_exit_reason", "ix_pt_instrument_seg",
        "ix_pt_conf_bucket", "ix_pt_regime_entry", "ix_pt_strategy_name",
    ]:
        op.drop_index(idx, "paper_trades")

    for col_name, _ in _ATTRIBUTION_COLS:
        op.drop_column("paper_trades", col_name)
