"""Initial business schema with forced tenant RLS and Chinese token FTS."""
from alembic import op
from pathlib import Path
import runpy

# Immutable schema snapshot: later application model edits must not rewrite migration history.
Base = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'schema_v1.py'))['Base']

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    Base.metadata.create_all(op.get_bind(), checkfirst=False)
    for name in Base.metadata.tables:
        column = 'id' if name == 'tenants' else 'tenant_id'
        op.execute(f'ALTER TABLE "{name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{name}" FORCE ROW LEVEL SECURITY')
        op.execute(f'''CREATE POLICY tenant_isolation ON "{name}"
            USING ({column} = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK ({column} = NULLIF(current_setting('app.tenant_id', true), '')::uuid)''')
    op.execute("""CREATE FUNCTION chunks_search_update() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN NEW.search_vector :=
            setweight(to_tsvector('simple', coalesce(array_to_string(NEW.section_path, ' '), '')), 'A') ||
            setweight(to_tsvector('simple', coalesce(NEW.search_tokens, '')), 'B');
        RETURN NEW; END $$""")
    op.execute('''CREATE TRIGGER chunks_search BEFORE INSERT OR UPDATE OF search_tokens, section_path
                  ON chunks FOR EACH ROW EXECUTE FUNCTION chunks_search_update()''')


def downgrade():
    op.execute('DROP TRIGGER chunks_search ON chunks')
    op.execute('DROP FUNCTION chunks_search_update()')
    Base.metadata.drop_all(op.get_bind(), checkfirst=False)
