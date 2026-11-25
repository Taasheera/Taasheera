"""Email copies of notifications (CMPS271-98): each notification's email status and tries."""

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision = "d4a8e2b6c931"
down_revision = "c7d2f9a4e815"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("notification") as batch:
        # Notifications that already exist were never meant to be emailed.
        batch.add_column(sa.Column("email_status", sqlmodel.sql.sqltypes.AutoString(length=10),
                                   server_default="skipped", nullable=False))
        batch.add_column(sa.Column("email_attempts", sa.Integer(), server_default="0", nullable=False))
        batch.create_index(batch.f("ix_notification_email_status"), ["email_status"], unique=False)


def downgrade():
    with op.batch_alter_table("notification") as batch:
        batch.drop_index(batch.f("ix_notification_email_status"))
        batch.drop_column("email_attempts")
        batch.drop_column("email_status")
