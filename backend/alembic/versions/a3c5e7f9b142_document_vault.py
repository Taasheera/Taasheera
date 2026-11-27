"""Reuse vault documents across applications (CMPS271-34) and declare blank passport pages (CMPS271-32)."""

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision = "a3c5e7f9b142"
down_revision = "a4c19e7d2b58"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("supportingdocument") as batch:
        batch.add_column(sa.Column("file_id", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=True))
        batch.create_index(batch.f("ix_supportingdocument_file_id"), ["file_id"], unique=False)
    op.execute("UPDATE supportingdocument SET file_id = id WHERE file_id IS NULL")
    op.create_table(
        "passportpages",
        sa.Column("traveler_id", sa.Integer(), nullable=False),
        sa.Column("blank_pages", sa.Integer(), nullable=False),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["traveler_id"], ["traveler.id"]),
        sa.PrimaryKeyConstraint("traveler_id"),
    )


def downgrade():
    op.drop_table("passportpages")
    with op.batch_alter_table("supportingdocument") as batch:
        batch.drop_index(batch.f("ix_supportingdocument_file_id"))
        batch.drop_column("file_id")
