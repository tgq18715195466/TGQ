"""Add tenant-isolated credential lookup for local username/password login."""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'login_credentials',
        sa.Column('id', sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column('tenant_id', sa.Uuid(as_uuid=False), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('username', sa.String(64), nullable=False),
        sa.Column('password_hash', sa.String(256), nullable=False),
        sa.Column('subject_tenant_id', sa.Uuid(as_uuid=False), nullable=False),
        sa.Column('user_id', sa.Uuid(as_uuid=False), nullable=False),
        sa.Column('failed_attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('locked_until', sa.DateTime(timezone=True)),
        sa.UniqueConstraint('tenant_id', 'id'),
        sa.UniqueConstraint('username', name='uq_login_credentials_username'),
    )
    op.create_index('ix_login_credentials_tenant_id', 'login_credentials', ['tenant_id'])
    op.execute('ALTER TABLE login_credentials ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE login_credentials FORCE ROW LEVEL SECURITY')
    op.execute("""CREATE POLICY tenant_isolation ON login_credentials
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)""")


def downgrade():
    op.drop_index('ix_login_credentials_tenant_id', table_name='login_credentials')
    op.drop_table('login_credentials')
