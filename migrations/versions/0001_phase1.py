"""Phase 1: screens, specs, bookings, import, sync, activity

Revision ID: 0001
Revises: 
Create Date: 2026-11-07 19:15:55.091318
"""
from alembic import op
import sqlalchemy as sa


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('activity',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('at', sa.DateTime(), nullable=False),
    sa.Column('actor', sa.String(length=254), nullable=False),
    sa.Column('action', sa.String(length=60), nullable=False),
    sa.Column('entity_type', sa.String(length=40), nullable=False),
    sa.Column('entity_id', sa.String(length=60), nullable=False),
    sa.Column('summary', sa.String(length=500), nullable=False),
    sa.Column('changes', sa.JSON(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('activity', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_activity_at'), ['at'], unique=False)
        batch_op.create_index('ix_activity_entity', ['entity_type', 'entity_id'], unique=False)

    op.create_table('booking_counters',
    sa.Column('year', sa.Integer(), nullable=False),
    sa.Column('last', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('year')
    )
    op.create_table('clients',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('contact_name', sa.String(length=200), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('phone', sa.String(length=40), nullable=False),
    sa.Column('whatsapp', sa.String(length=40), nullable=False),
    sa.Column('language', sa.String(length=2), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('import_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('source_kind', sa.String(length=20), nullable=False),
    sa.Column('source_label', sa.String(length=500), nullable=False),
    sa.Column('spreadsheet_id', sa.String(length=200), nullable=False),
    sa.Column('tab', sa.String(length=200), nullable=False),
    sa.Column('header', sa.JSON(), nullable=False),
    sa.Column('rows', sa.JSON(), nullable=False),
    sa.Column('mapping', sa.JSON(), nullable=False),
    sa.Column('preview', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_by', sa.String(length=254), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('committed_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('jobs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('dedupe_key', sa.String(length=200), nullable=False),
    sa.Column('run_at', sa.DateTime(), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('last_error', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.create_index('ix_jobs_dedupe', ['dedupe_key', 'status'], unique=False)
        batch_op.create_index('ix_jobs_due', ['status', 'run_at'], unique=False)

    op.create_table('notifications',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_email', sa.String(length=254), nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('text', sa.String(length=500), nullable=False),
    sa.Column('link', sa.String(length=300), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('read_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_notifications_created_at'), ['created_at'], unique=False)

    op.create_table('settings',
    sa.Column('key', sa.String(length=80), nullable=False),
    sa.Column('value', sa.JSON(), nullable=True),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('specs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('width_px', sa.Integer(), nullable=False),
    sa.Column('height_px', sa.Integer(), nullable=False),
    sa.Column('file_types', sa.String(length=200), nullable=False),
    sa.Column('video_codecs', sa.String(length=200), nullable=False),
    sa.Column('durations_s', sa.String(length=100), nullable=False),
    sa.Column('duration_tolerance_s', sa.Float(), nullable=False),
    sa.Column('fps_min', sa.Float(), nullable=True),
    sa.Column('fps_max', sa.Float(), nullable=True),
    sa.Column('max_file_mb', sa.Float(), nullable=True),
    sa.Column('audio_allowed', sa.Boolean(), nullable=False),
    sa.Column('safe_margin_pct', sa.Float(), nullable=False),
    sa.Column('language_rule', sa.String(length=40), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('bookings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('code', sa.String(length=20), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('hold_expires_at', sa.DateTime(), nullable=True),
    sa.Column('hold_reminded_at', sa.DateTime(), nullable=True),
    sa.Column('confirmed_at', sa.DateTime(), nullable=True),
    sa.Column('confirmed_by', sa.String(length=254), nullable=False),
    sa.Column('cancel_reason', sa.String(length=500), nullable=False),
    sa.Column('override_reason', sa.String(length=500), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('source_ref', sa.String(length=300), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('created_by', sa.String(length=254), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    with op.batch_alter_table('bookings', schema=None) as batch_op:
        batch_op.create_index('ix_bookings_source_ref', ['source_ref'], unique=False)

    op.create_table('screens',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('code', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('area', sa.String(length=120), nullable=False),
    sa.Column('address', sa.String(length=300), nullable=False),
    sa.Column('lat', sa.Float(), nullable=True),
    sa.Column('lng', sa.Float(), nullable=True),
    sa.Column('physical_size', sa.String(length=60), nullable=False),
    sa.Column('spec_id', sa.Integer(), nullable=True),
    sa.Column('loop_seconds', sa.Integer(), nullable=False),
    sa.Column('slot_seconds', sa.Integer(), nullable=False),
    sa.Column('sellable_pct', sa.Float(), nullable=False),
    sa.Column('open_hour', sa.Integer(), nullable=False),
    sa.Column('close_hour', sa.Integer(), nullable=False),
    sa.Column('timezone', sa.String(length=60), nullable=False),
    sa.Column('calendar_id', sa.String(length=300), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['spec_id'], ['specs.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    op.create_table('sheet_watches',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('import_run_id', sa.Integer(), nullable=False),
    sa.Column('spreadsheet_id', sa.String(length=200), nullable=False),
    sa.Column('tab', sa.String(length=200), nullable=False),
    sa.Column('key_column', sa.Integer(), nullable=True),
    sa.Column('row_hashes', sa.JSON(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('last_checked_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['import_run_id'], ['import_runs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=True),
    sa.Column('language', sa.String(length=2), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('last_login_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_table('blackouts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('screen_id', sa.Integer(), nullable=True),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('end_date', sa.Date(), nullable=False),
    sa.Column('reason', sa.String(length=300), nullable=False),
    sa.ForeignKeyConstraint(['screen_id'], ['screens.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('booking_lines',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('booking_id', sa.Integer(), nullable=False),
    sa.Column('screen_id', sa.Integer(), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('end_date', sa.Date(), nullable=False),
    sa.Column('start_hour', sa.Integer(), nullable=True),
    sa.Column('end_hour', sa.Integer(), nullable=True),
    sa.Column('ad_seconds', sa.Integer(), nullable=False),
    sa.Column('plays_per_hour', sa.Float(), nullable=False),
    sa.Column('exclusive', sa.Boolean(), nullable=False),
    sa.Column('calendar_event_id', sa.String(length=300), nullable=False),
    sa.ForeignKeyConstraint(['booking_id'], ['bookings.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['screen_id'], ['screens.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('booking_lines', schema=None) as batch_op:
        batch_op.create_index('ix_lines_screen_dates', ['screen_id', 'start_date', 'end_date'], unique=False)

    op.create_table('hours_overrides',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('screen_id', sa.Integer(), nullable=True),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('end_date', sa.Date(), nullable=False),
    sa.Column('open_hour', sa.Integer(), nullable=False),
    sa.Column('close_hour', sa.Integer(), nullable=False),
    sa.Column('reason', sa.String(length=300), nullable=False),
    sa.ForeignKeyConstraint(['screen_id'], ['screens.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('sheet_changes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('watch_id', sa.Integer(), nullable=False),
    sa.Column('row_key', sa.String(length=300), nullable=False),
    sa.Column('change', sa.String(length=20), nullable=False),
    sa.Column('before', sa.JSON(), nullable=True),
    sa.Column('after', sa.JSON(), nullable=True),
    sa.Column('detected_at', sa.DateTime(), nullable=False),
    sa.Column('resolved_at', sa.DateTime(), nullable=True),
    sa.Column('resolved_by', sa.String(length=254), nullable=False),
    sa.ForeignKeyConstraint(['watch_id'], ['sheet_watches.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade():
    op.drop_table('sheet_changes')
    op.drop_table('hours_overrides')
    with op.batch_alter_table('booking_lines', schema=None) as batch_op:
        batch_op.drop_index('ix_lines_screen_dates')

    op.drop_table('booking_lines')
    op.drop_table('blackouts')
    op.drop_table('users')
    op.drop_table('sheet_watches')
    op.drop_table('screens')
    with op.batch_alter_table('bookings', schema=None) as batch_op:
        batch_op.drop_index('ix_bookings_source_ref')

    op.drop_table('bookings')
    op.drop_table('specs')
    op.drop_table('settings')
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_notifications_created_at'))

    op.drop_table('notifications')
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_index('ix_jobs_due')
        batch_op.drop_index('ix_jobs_dedupe')

    op.drop_table('jobs')
    op.drop_table('import_runs')
    op.drop_table('clients')
    op.drop_table('booking_counters')
    with op.batch_alter_table('activity', schema=None) as batch_op:
        batch_op.drop_index('ix_activity_entity')
        batch_op.drop_index(batch_op.f('ix_activity_at'))

    op.drop_table('activity')
