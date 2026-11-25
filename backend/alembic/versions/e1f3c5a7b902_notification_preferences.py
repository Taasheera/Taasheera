"""Which notification emails each traveler wants (CMPS271-99)."""

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision = "e1f3c5a7b902"
down_revision = "d4a8e2b6c931"
branch_labels = None
depends_on = None


def upgrade():
    # Starts empty: no row means every email is on.
    op.create_table(
        "notificationpreference",
        sa.Column("traveler_id", sa.Integer(), nullable=False),
        sa.Column("application_updates", sa.Boolean(), nullable=False),
        sa.Column("reminders", sa.Boolean(), nullable=False),
        sa.Column("passport_expiry", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["traveler_id"], ["traveler.id"]),
        sa.PrimaryKeyConstraint("traveler_id"),
    )


def downgrade():
    op.drop_table("notificationpreference")
