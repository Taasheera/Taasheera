"""Encrypted optional authenticator enrollment and one-use challenges.

Revision ID: f4b27a9c803e
Revises: e4a162f09b37
"""
from alembic import op
import sqlalchemy as sa

revision = "f4b27a9c803e"
down_revision = "e4a162f09b37"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("mfasettings",
        sa.Column("traveler_id", sa.Integer(), sa.ForeignKey("traveler.id"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("secret_ciphertext", sa.String(512), nullable=True),
        sa.Column("pending_ciphertext", sa.String(512), nullable=True),
        sa.Column("pending_expires_at", sa.Integer(), nullable=True),
        sa.Column("last_counter", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False))
    op.create_table("mfabackupcode",
        sa.Column("code_hash", sa.String(64), primary_key=True),
        sa.Column("traveler_id", sa.Integer(), sa.ForeignKey("traveler.id"), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False))
    op.create_index("ix_mfabackupcode_traveler_id", "mfabackupcode", ["traveler_id"])
    op.create_table("mfachallenge",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("traveler_id", sa.Integer(), sa.ForeignKey("traveler.id"), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False),
        sa.Column("method", sa.String(20), nullable=False),
        sa.Column("credential_fingerprint", sa.String(64), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False))
    op.create_index("ix_mfachallenge_traveler_id", "mfachallenge", ["traveler_id"])
    op.create_index("ix_mfachallenge_expires_at", "mfachallenge", ["expires_at"])


def downgrade():
    for table in ("mfachallenge", "mfabackupcode", "mfasettings"):
        op.drop_table(table)
