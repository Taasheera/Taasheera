"""Record which privacy policy version each traveler accepted, and when (CMPS271-115)."""

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision = "c7d2f9a4e815"
down_revision = "b8e4f1c07d23"
branch_labels = None
depends_on = None


def upgrade():
    # Starts empty: existing travelers are asked to accept on their next visit.
    op.create_table(
        "privacyconsent",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("traveler_id", sa.Integer(), nullable=False),
        sa.Column("policy_version", sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column("accepted_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["traveler_id"], ["traveler.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("traveler_id", "policy_version", name="uq_privacyconsent_traveler_version"),
    )
    with op.batch_alter_table("privacyconsent") as batch:
        batch.create_index(batch.f("ix_privacyconsent_traveler_id"), ["traveler_id"], unique=False)


def downgrade():
    with op.batch_alter_table("privacyconsent") as batch:
        batch.drop_index(batch.f("ix_privacyconsent_traveler_id"))
    op.drop_table("privacyconsent")
