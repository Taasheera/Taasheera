"""Let travelers record submitting an application and the embassy's decision (US 55)."""

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision = "b8e4f1c07d23"
down_revision = "e3a9c7d15f20"
branch_labels = None
depends_on = None


def upgrade():
    # Nullable with no default: no existing application has been recorded as submitted.
    with op.batch_alter_table("application") as batch:
        batch.add_column(sa.Column("submitted_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True))
        batch.add_column(sa.Column("decided_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table("application") as batch:
        batch.drop_column("decided_at")
        batch.drop_column("submitted_at")
