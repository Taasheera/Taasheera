"""The embassy appointment, its reminders and the first packet download (CMPS271-72/73/77/79)."""

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision = "c4e8a1d3f592"
down_revision = "b7d2f4a6c813"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "applicationtracking",
        sa.Column("application_id", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("traveler_id", sa.Integer(), nullable=False),
        sa.Column("appointment_at", sa.DateTime(), nullable=True),
        sa.Column("appointment_location", sqlmodel.sql.sqltypes.AutoString(length=200), nullable=True),
        sa.Column("appointment_reminded_days", sa.Integer(), nullable=True),
        sa.Column("packet_downloaded_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["application_id"], ["application.id"]),
        sa.ForeignKeyConstraint(["traveler_id"], ["traveler.id"]),
        sa.PrimaryKeyConstraint("application_id"),
    )
    op.create_index("ix_applicationtracking_traveler_id", "applicationtracking", ["traveler_id"])
    op.create_index("ix_applicationtracking_appointment_at", "applicationtracking", ["appointment_at"])


def downgrade():
    op.drop_index("ix_applicationtracking_appointment_at", table_name="applicationtracking")
    op.drop_index("ix_applicationtracking_traveler_id", table_name="applicationtracking")
    op.drop_table("applicationtracking")
