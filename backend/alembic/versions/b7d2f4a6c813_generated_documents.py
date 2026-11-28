"""The traveler's edits of generated documents: cover letter, itinerary and letter templates (CMPS271-57/58)."""

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision = "b7d2f4a6c813"
down_revision = "a3c9e5f17b42"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "generateddocument",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("traveler_id", sa.Integer(), nullable=False),
        sa.Column("application_id", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("kind", sqlmodel.sql.sqltypes.AutoString(length=30), nullable=False),
        sa.Column("language", sqlmodel.sql.sqltypes.AutoString(length=5), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["application.id"]),
        sa.ForeignKeyConstraint(["traveler_id"], ["traveler.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", "kind", "language", name="uq_generateddocument_application_kind_language"),
    )
    op.create_index("ix_generateddocument_traveler_id", "generateddocument", ["traveler_id"])
    op.create_index("ix_generateddocument_application_id", "generateddocument", ["application_id"])


def downgrade():
    op.drop_index("ix_generateddocument_application_id", table_name="generateddocument")
    op.drop_index("ix_generateddocument_traveler_id", table_name="generateddocument")
    op.drop_table("generateddocument")
