from alembic import command
from sqlalchemy import text

from app.database import build_engine
from app.schema import migration_config


def test_legacy_document_and_admin_metadata_is_backfilled_without_duplicate_access(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', f"sqlite:///{tmp_path / 'legacy.db'}")
    engine = build_engine()
    with engine.begin() as connection:
        config = migration_config(); config.attributes['connection'] = connection
        command.upgrade(config, 'd5e2a7c41f86')
        connection.execute(text("INSERT INTO traveler(id,name,email,password_hash,auth_type) VALUES (1,'Owner','owner@example.com','synthetic','email_password'),(2,'Staff','staff@example.com','synthetic','email_password')"))
        connection.execute(text("INSERT INTO activityevent(traveler_id,occurred_at,event_type,identifier,outcome) VALUES (1,'2026-11-03 10:00:00','document_access','passport','success'),(1,'2026-11-03 11:00:00','role_change','admin:2:traveler->admin','success')"))
        connection.execute(text("INSERT INTO documentaccess(traveler_id,actor_id,upload_id,purpose,occurred_at) VALUES (1,1,:upload,'image_view','2026-11-03 10:00:00')"), {'upload': 'a' * 32})
        command.upgrade(config, 'head')
        rows = connection.execute(text('SELECT * FROM activityevent ORDER BY id')).mappings().all()
        assert len(rows) == 2
        assert rows[0]['document_access_id'] == 1 and rows[0]['resource_id'] == 'a' * 32
        assert rows[0]['purpose'] == 'image_view'
        assert rows[1]['actor_id'] == 2 and rows[1]['actor_kind'] == 'staff'
    engine.dispose()
