"""The expiry date of each traveler's confirmed passport, for reminders (CMPS271-36)."""

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision = "f6a2d8c4e317"
down_revision = "e1f3c5a7b902"
branch_labels = None
depends_on = None


def upgrade():
    # Starts empty: a date is saved when a traveler next confirms their passport.
    op.create_table(
        "passportexpiry",
        sa.Column("traveler_id", sa.Integer(), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("reminded_days", sa.Integer(), nullable=True),
        sa.Column("recorded_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["traveler_id"], ["traveler.id"]),
        sa.PrimaryKeyConstraint("traveler_id"),
    )


def downgrade():
    op.drop_table("passportexpiry")
