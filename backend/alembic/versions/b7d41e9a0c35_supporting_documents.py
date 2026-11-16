"""Supporting documents uploaded for an application's requirements (CMPS271-29)."""

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision = "b7d41e9a0c35"
down_revision = "d5e2a7c41f86"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "supportingdocument",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("traveler_id", sa.Integer(), nullable=False),
        sa.Column("application_id", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("requirement", sqlmodel.sql.sqltypes.AutoString(length=40), nullable=False),
        sa.Column("filename", sqlmodel.sql.sqltypes.AutoString(length=120), nullable=False),
        sa.Column("content_type", sqlmodel.sql.sqltypes.AutoString(length=40), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("uploaded_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["application.id"]),
        sa.ForeignKeyConstraint(["traveler_id"], ["traveler.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("supportingdocument") as batch:
        batch.create_index(batch.f("ix_supportingdocument_traveler_id"), ["traveler_id"], unique=False)
        batch.create_index(batch.f("ix_supportingdocument_application_id"), ["application_id"], unique=False)


def downgrade():
    with op.batch_alter_table("supportingdocument") as batch:
        batch.drop_index(batch.f("ix_supportingdocument_application_id"))
        batch.drop_index(batch.f("ix_supportingdocument_traveler_id"))
    op.drop_table("supportingdocument")
