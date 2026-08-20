"""tactical_signals — Path F audit log

Path F (Tactical pipeline) writes to exactly one table; everything else it
touches is read-only. Deliberately Alembic-only: the table is NOT added to
db/database.py::init_db(), because maintaining two parallel schema paths is
audit finding D10 and this is a chance not to make it worse.

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa

revision      = "0006"
down_revision = "0005"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    op.create_table(
        "tactical_signals",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("timestamp", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("strategy", sa.String(length=50), nullable=False),
        sa.Column("sub_pipeline", sa.String(length=4), server_default="F1", nullable=False),
        sa.Column("signal_type", sa.String(length=10), nullable=False),
        sa.Column("entry_price", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("stop_loss", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("target", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("composite_score", sa.Float(), nullable=True),
        sa.Column("ml_prob", sa.Float(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("risk_amount", sa.Float(), nullable=True),
        sa.Column("executed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("meta_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tactical_signals_symbol", "tactical_signals", ["symbol"])
    op.create_index("ix_tactical_symbol_ts", "tactical_signals", ["symbol", "timestamp"])
    op.create_index("ix_tactical_strategy_ts", "tactical_signals", ["strategy", "timestamp"])


def downgrade() -> None:
    op.drop_index("ix_tactical_strategy_ts", table_name="tactical_signals")
    op.drop_index("ix_tactical_symbol_ts", table_name="tactical_signals")
    op.drop_index("ix_tactical_signals_symbol", table_name="tactical_signals")
    op.drop_table("tactical_signals")
