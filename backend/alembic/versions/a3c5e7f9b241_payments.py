"""Payments for an application's submission, simulated for now (CMPS271-106, 108, 109)."""

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision = "a3c5e7f9b241"
down_revision = "f6a2d8c4e317"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payment",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("traveler_id", sa.Integer(), nullable=False),
        sa.Column("application_id", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sqlmodel.sql.sqltypes.AutoString(length=3), nullable=False),
        sa.Column("line_items", sa.JSON(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(length=10), nullable=False),
        sa.Column("provider", sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column("provider_reference", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
        sa.Column("idempotency_key", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("paid_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'paid', 'failed')", name="ck_payment_status"),
        sa.ForeignKeyConstraint(["application_id"], ["application.id"]),
        sa.ForeignKeyConstraint(["traveler_id"], ["traveler.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", "idempotency_key", name="uq_payment_application_idempotency"),
    )
    with op.batch_alter_table("payment") as batch:
        batch.create_index(batch.f("ix_payment_traveler_id"), ["traveler_id"], unique=False)
        batch.create_index(batch.f("ix_payment_application_id"), ["application_id"], unique=False)
        batch.create_index(batch.f("ix_payment_status"), ["status"], unique=False)
        batch.create_index(batch.f("ix_payment_created_at"), ["created_at"], unique=False)


def downgrade():
    with op.batch_alter_table("payment") as batch:
        batch.drop_index(batch.f("ix_payment_created_at"))
        batch.drop_index(batch.f("ix_payment_status"))
        batch.drop_index(batch.f("ix_payment_application_id"))
        batch.drop_index(batch.f("ix_payment_traveler_id"))
    op.drop_table("payment")
