"""Support access grants, support requests and editable FAQ entries (CMPS271-128/92/129).

The FAQ starts with the questions frontend/src/content.ts showed before, published in English, so
nothing disappears on release.
"""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision = "a3c9e5f17b42"
down_revision = "a3c5e7f9b142"
branch_labels = None
depends_on = None


FAQ_SEED = [
    ("What does Taasheera do?",
     "Taasheera reads your passport, asks questions about your trip and checks your saved answers for missing or "
     "inconsistent information. You review the details before taking them to the embassy."),
    ("Will anything be submitted without my approval?",
     "You decide when to submit. Taasheera prepares your information for review, and you submit it through your "
     "consulate’s process."),
    ("Which destinations can I apply for?",
     "Guided questionnaires are available for Schengen short stays, the UK Standard Visitor visa, the US B-2 visa, "
     "the Canadian visitor visa, the Japanese short-term stay visa, the Chinese tourist (L) visa, India's tourist and "
     "e-Tourist visas and the Türkiye e-Visa. Schengen gets a filled official PDF; the others get a step-by-step "
     "guide to the official form."),
    ("How do I use my application at the embassy?",
     "Follow the instructions from your consulate. When PDF preparation is available for your Schengen application, "
     "you can compare each prefilled value with your saved answers, download the form, then complete and sign it."),
    ("How do you read my passport?",
     "We read the machine-readable zone, the two lines of text at the bottom of your passport's photo page, and "
     "verify it with its built-in check digits. Some mistakes can still slip through, so we always ask you to "
     "confirm each field."),
    ("What if my passport photo can't be read?",
     "Retake the photo in even light with the entire passport page visible, or type the details by hand in the "
     "application."),
    ("Is my passport data safe?",
     "Your passport and documents are stored privately and are only ever available to your account. Photos are "
     "re-encoded when you upload them, which removes hidden location data. Sign-in tokens are never kept in your "
     "browser's storage."),
    ("Do I still need to go to the embassy?",
     "Your consulate may ask for an interview or fingerprints in person. Check its instructions for how to book and "
     "what to bring."),
    ("Does Taasheera guarantee my visa?",
     "Only the embassy or consulate decides whether to issue a visa. Our checks help you catch missing or "
     "inconsistent information before you submit."),
    ("Is this legal or immigration advice?",
     "No. Taasheera is a preparation tool, not a lawyer or an immigration adviser. For complex cases, such as a "
     "previous refusal or overstay, speak to a qualified adviser or the embassy directly."),
]


def upgrade():
    op.create_table(
        "supportaccessgrant",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("traveler_id", sa.Integer(), nullable=False),
        sa.Column("application_id", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("expires_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("revoked_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
        sa.ForeignKeyConstraint(["application_id"], ["application.id"]),
        sa.ForeignKeyConstraint(["traveler_id"], ["traveler.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("supportaccessgrant") as batch:
        batch.create_index(batch.f("ix_supportaccessgrant_traveler_id"), ["traveler_id"], unique=False)
        batch.create_index(batch.f("ix_supportaccessgrant_application_id"), ["application_id"], unique=False)
        batch.create_index(batch.f("ix_supportaccessgrant_expires_at"), ["expires_at"], unique=False)

    op.create_table(
        "supportrequest",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("traveler_id", sa.Integer(), nullable=False),
        sa.Column("application_id", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=True),
        sa.Column("source", sqlmodel.sql.sqltypes.AutoString(length=10), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column("assignee_id", sa.Integer(), nullable=True),
        sa.Column("reply", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("replied_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
        sa.Column("resolved_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
        sa.CheckConstraint("status IN ('open', 'in_progress', 'resolved')", name="ck_supportrequest_status"),
        sa.CheckConstraint("source IN ('website', 'telegram')", name="ck_supportrequest_source"),
        sa.ForeignKeyConstraint(["application_id"], ["application.id"]),
        sa.ForeignKeyConstraint(["assignee_id"], ["traveler.id"]),
        sa.ForeignKeyConstraint(["traveler_id"], ["traveler.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("supportrequest") as batch:
        batch.create_index(batch.f("ix_supportrequest_traveler_id"), ["traveler_id"], unique=False)
        batch.create_index(batch.f("ix_supportrequest_application_id"), ["application_id"], unique=False)
        batch.create_index(batch.f("ix_supportrequest_status"), ["status"], unique=False)
        batch.create_index(batch.f("ix_supportrequest_assignee_id"), ["assignee_id"], unique=False)
        batch.create_index(batch.f("ix_supportrequest_created_at"), ["created_at"], unique=False)

    faq = op.create_table(
        "faqentry",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("question", sqlmodel.sql.sqltypes.AutoString(length=300), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("category", sqlmodel.sql.sqltypes.AutoString(length=60), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("published", sa.Boolean(), nullable=False),
        sa.Column("language", sqlmodel.sql.sqltypes.AutoString(length=2), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.CheckConstraint("language IN ('en', 'ar')", name="ck_faqentry_language"),
        sa.ForeignKeyConstraint(["updated_by"], ["traveler.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("faqentry") as batch:
        batch.create_index(batch.f("ix_faqentry_language"), ["language"], unique=False)
        batch.create_index(batch.f("ix_faqentry_updated_by"), ["updated_by"], unique=False)

    now = datetime.now(timezone.utc)
    op.bulk_insert(faq, [
        {"question": question, "answer": answer, "category": "General", "position": position, "published": True,
         "language": "en", "updated_by": None, "created_at": now, "updated_at": now}
        for position, (question, answer) in enumerate(FAQ_SEED, start=1)
    ])


def downgrade():
    with op.batch_alter_table("faqentry") as batch:
        batch.drop_index(batch.f("ix_faqentry_updated_by"))
        batch.drop_index(batch.f("ix_faqentry_language"))
    op.drop_table("faqentry")
    with op.batch_alter_table("supportrequest") as batch:
        batch.drop_index(batch.f("ix_supportrequest_created_at"))
        batch.drop_index(batch.f("ix_supportrequest_assignee_id"))
        batch.drop_index(batch.f("ix_supportrequest_status"))
        batch.drop_index(batch.f("ix_supportrequest_application_id"))
        batch.drop_index(batch.f("ix_supportrequest_traveler_id"))
    op.drop_table("supportrequest")
    with op.batch_alter_table("supportaccessgrant") as batch:
        batch.drop_index(batch.f("ix_supportaccessgrant_expires_at"))
        batch.drop_index(batch.f("ix_supportaccessgrant_application_id"))
        batch.drop_index(batch.f("ix_supportaccessgrant_traveler_id"))
    op.drop_table("supportaccessgrant")
