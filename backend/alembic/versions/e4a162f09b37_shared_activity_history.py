"""Shared activity metadata for admin history and document access.

Revision ID: e4a162f09b37
Revises: d5e2a7c41f86
"""
from alembic import op
import sqlalchemy as sa

revision = "e4a162f09b37"
down_revision = "d5e2a7c41f86"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("activityevent") as batch:
        batch.add_column(sa.Column("actor_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("actor_kind", sa.String(20), server_default="traveler", nullable=False))
        batch.add_column(sa.Column("resource_type", sa.String(30), nullable=True))
        batch.add_column(sa.Column("resource_id", sa.String(64), nullable=True))
        batch.add_column(sa.Column("purpose", sa.String(30), nullable=True))
        batch.add_column(sa.Column("document_access_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_activity_actor", "traveler", ["actor_id"], ["id"], ondelete="SET NULL")
        batch.create_foreign_key("fk_activity_document_access", "documentaccess", ["document_access_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_activityevent_actor_id", ["actor_id"])
        batch.create_index("ix_activityevent_document_access_id", ["document_access_id"], unique=True)
    connection = op.get_bind()
    connection.execute(sa.text("UPDATE activityevent SET actor_id=traveler_id"))
    # These historical identifiers were emitted by trusted admin/CLI code.
    # Do not misattribute a staff change to the traveler whose account changed.
    import re
    changes = connection.execute(sa.text("SELECT id, identifier FROM activityevent WHERE event_type IN ('role_change','status_change')")).mappings()
    for change in changes:
        match = re.match(r"^admin:(\d+):", change["identifier"])
        actor = int(match[1]) if match else None
        if actor is not None and not connection.execute(sa.text("SELECT id FROM traveler WHERE id=:id"), {"id": actor}).first():
            actor = None
        kind = "service" if change["identifier"].startswith("cli:") else "staff"
        connection.execute(sa.text("UPDATE activityevent SET actor_id=:actor, actor_kind=:kind WHERE id=:id"),
                           {"actor": actor, "kind": kind, "id": change["id"]})
    # Old document hooks committed one access row and one generic activity row
    # together. Their per-owner insertion orders match. Pair only complete,
    # temporally matching sequences; preserve ambiguous legacy logs unchanged.
    activity = sa.table("activityevent", sa.column("id"), sa.column("traveler_id"), sa.column("occurred_at"),
        sa.column("event_type"), sa.column("identifier"), sa.column("outcome"), sa.column("actor_id"),
        sa.column("actor_kind"), sa.column("resource_type"), sa.column("resource_id"),
        sa.column("purpose"), sa.column("document_access_id"))
    accesses = connection.execute(sa.text("SELECT * FROM documentaccess ORDER BY id")).mappings().all()
    owners = sorted({row["traveler_id"] for row in accesses})
    from datetime import datetime, timezone
    def utc(value):
        if isinstance(value, str): value = datetime.fromisoformat(value)
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    for owner in owners:
        documents = [row for row in accesses if row["traveler_id"] == owner]
        events = connection.execute(sa.select(activity).where(activity.c.traveler_id == owner,
            activity.c.event_type == "document_access").order_by(activity.c.id)).mappings().all()
        paired = (len(events) == len(documents) and all(
            abs((utc(event["occurred_at"]) - utc(document["occurred_at"])).total_seconds()) < 1
            for event, document in zip(events, documents)))
        for index, document in enumerate(documents):
            fields = {"actor_id": document["actor_id"], "actor_kind": "traveler" if document["actor_id"] == owner else "staff",
                "resource_type": "passport", "resource_id": document["upload_id"],
                "purpose": document["purpose"], "document_access_id": document["id"]}
            if paired:
                connection.execute(sa.update(activity).where(activity.c.id == events[index]["id"]).values(**fields))
            else:
                connection.execute(sa.insert(activity).values(traveler_id=owner,
                    occurred_at=document["occurred_at"], event_type="document_access", identifier="passport",
                    outcome="success", **fields))


def downgrade():
    # Metadata added for an unmatched legacy access is useful only while its
    # columns exist. Retain all audit rows rather than silently discard history.
    with op.batch_alter_table("activityevent") as batch:
        batch.drop_index("ix_activityevent_document_access_id")
        batch.drop_index("ix_activityevent_actor_id")
        batch.drop_constraint("fk_activity_document_access", type_="foreignkey")
        batch.drop_constraint("fk_activity_actor", type_="foreignkey")
        for column in ("document_access_id", "purpose", "resource_id", "resource_type", "actor_kind", "actor_id"):
            batch.drop_column(column)
