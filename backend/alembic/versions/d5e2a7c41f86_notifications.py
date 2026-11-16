"""In-app notifications per traveler, optionally about one application (US 28, US 107)."""

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision = "d5e2a7c41f86"
down_revision = "c4a8d2f61b93"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "notification",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("traveler_id", sa.Integer(), nullable=False),
        sa.Column("application_id", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=True),
        sa.Column("kind", sqlmodel.sql.sqltypes.AutoString(length=30), nullable=False),
        sa.Column("message", sqlmodel.sql.sqltypes.AutoString(length=300), nullable=False),
        sa.Column("link", sqlmodel.sql.sqltypes.AutoString(length=200), nullable=True),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("read_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
        sa.ForeignKeyConstraint(["application_id"], ["application.id"]),
        sa.ForeignKeyConstraint(["traveler_id"], ["traveler.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("notification") as batch:
        batch.create_index(batch.f("ix_notification_traveler_id"), ["traveler_id"], unique=False)
        batch.create_index(batch.f("ix_notification_application_id"), ["application_id"], unique=False)
        batch.create_index(batch.f("ix_notification_created_at"), ["created_at"], unique=False)


def downgrade():
    with op.batch_alter_table("notification") as batch:
        batch.drop_index(batch.f("ix_notification_created_at"))
        batch.drop_index(batch.f("ix_notification_application_id"))
        batch.drop_index(batch.f("ix_notification_traveler_id"))
    op.drop_table("notification")
