"""Group trips: several travelers' applications under one account (CMPS271-81)."""

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision = "d5f9b2e4a713"
down_revision = "c4e8a1d3f592"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "groupmember",
        sa.Column("application_id", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("traveler_id", sa.Integer(), nullable=False),
        sa.Column("group_id", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("label", sqlmodel.sql.sqltypes.AutoString(length=60), nullable=False),
        sa.Column("lead", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["application.id"]),
        sa.ForeignKeyConstraint(["traveler_id"], ["traveler.id"]),
        sa.PrimaryKeyConstraint("application_id"),
    )
    op.create_index("ix_groupmember_traveler_id", "groupmember", ["traveler_id"])
    op.create_index("ix_groupmember_group_id", "groupmember", ["group_id"])


def downgrade():
    op.drop_index("ix_groupmember_group_id", table_name="groupmember")
    op.drop_index("ix_groupmember_traveler_id", table_name="groupmember")
    op.drop_table("groupmember")
