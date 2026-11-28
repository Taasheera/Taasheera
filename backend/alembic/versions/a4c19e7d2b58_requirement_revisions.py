"""Destination requirements published from the back-office, and their drafts (CMPS271-123)."""

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision = "a4c19e7d2b58"
down_revision = "a3c5e7f9b241"
branch_labels = None
depends_on = None


def upgrade():
    # Both start empty: every destination keeps its packaged files until an admin publishes.
    op.create_table(
        "requirementrevision",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("questionnaire_id", sqlmodel.sql.sqltypes.AutoString(length=60), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("kind", sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column("restored_revision", sa.Integer(), nullable=True),
        sa.Column("questionnaire", sa.JSON(), nullable=True),
        sa.Column("guide", sa.JSON(), nullable=True),
        sa.Column("questionnaire_version", sa.Integer(), nullable=False),
        sa.Column("note", sqlmodel.sql.sqltypes.AutoString(length=500), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["author_id"], ["traveler.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("questionnaire_id", "revision", name="uq_requirementrevision_revision"),
    )
    with op.batch_alter_table("requirementrevision") as batch:
        batch.create_index(batch.f("ix_requirementrevision_questionnaire_id"), ["questionnaire_id"], unique=False)
        batch.create_index(batch.f("ix_requirementrevision_author_id"), ["author_id"], unique=False)
    op.create_table(
        "requirementdraft",
        sa.Column("questionnaire_id", sqlmodel.sql.sqltypes.AutoString(length=60), nullable=False),
        sa.Column("questionnaire", sa.JSON(), nullable=False),
        sa.Column("guide", sa.JSON(), nullable=False),
        sa.Column("base_revision", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=True),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["author_id"], ["traveler.id"]),
        sa.PrimaryKeyConstraint("questionnaire_id"),
    )
    with op.batch_alter_table("requirementdraft") as batch:
        batch.create_index(batch.f("ix_requirementdraft_author_id"), ["author_id"], unique=False)


def downgrade():
    with op.batch_alter_table("requirementdraft") as batch:
        batch.drop_index(batch.f("ix_requirementdraft_author_id"))
    op.drop_table("requirementdraft")
    with op.batch_alter_table("requirementrevision") as batch:
        batch.drop_index(batch.f("ix_requirementrevision_author_id"))
        batch.drop_index(batch.f("ix_requirementrevision_questionnaire_id"))
    op.drop_table("requirementrevision")
