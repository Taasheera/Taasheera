"""Form-filling agent runs, the fields they typed and their screenshots (CMPS271-236, 67, 70)."""

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision = "e3a9c7d15f20"
down_revision = "b7d41e9a0c35"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agentrun",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("traveler_id", sa.Integer(), nullable=False),
        sa.Column("application_id", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("site", sqlmodel.sql.sqltypes.AutoString(length=40), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column("message", sqlmodel.sql.sqltypes.AutoString(length=300), nullable=True),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("started_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
        sa.Column("finished_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
        sa.ForeignKeyConstraint(["application_id"], ["application.id"]),
        sa.ForeignKeyConstraint(["traveler_id"], ["traveler.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("agentrun") as batch:
        batch.create_index(batch.f("ix_agentrun_traveler_id"), ["traveler_id"], unique=False)
        batch.create_index(batch.f("ix_agentrun_application_id"), ["application_id"], unique=False)
        batch.create_index(batch.f("ix_agentrun_status"), ["status"], unique=False)
    op.create_table(
        "agentfieldentry",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("page_title", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
        sa.Column("field", sqlmodel.sql.sqltypes.AutoString(length=120), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("entered_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agentrun.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("agentfieldentry") as batch:
        batch.create_index(batch.f("ix_agentfieldentry_run_id"), ["run_id"], unique=False)
    op.create_table(
        "agentscreenshot",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("run_id", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("page_title", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
        sa.Column("taken_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agentrun.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("agentscreenshot") as batch:
        batch.create_index(batch.f("ix_agentscreenshot_run_id"), ["run_id"], unique=False)


def downgrade():
    with op.batch_alter_table("agentscreenshot") as batch:
        batch.drop_index(batch.f("ix_agentscreenshot_run_id"))
    op.drop_table("agentscreenshot")
    with op.batch_alter_table("agentfieldentry") as batch:
        batch.drop_index(batch.f("ix_agentfieldentry_run_id"))
    op.drop_table("agentfieldentry")
    with op.batch_alter_table("agentrun") as batch:
        batch.drop_index(batch.f("ix_agentrun_status"))
        batch.drop_index(batch.f("ix_agentrun_application_id"))
        batch.drop_index(batch.f("ix_agentrun_traveler_id"))
    op.drop_table("agentrun")
