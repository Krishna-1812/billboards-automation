"""Phase 4: play plans, getting ready, going live, proof of play and the client portal

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-08 08:00:11.756976
"""
from alembic import op
import sqlalchemy as sa


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('login_links',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('used_at', sa.DateTime(), nullable=True),
    sa.Column('created_by', sa.String(length=254), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_table('proofs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('booking_id', sa.Integer(), nullable=False),
    sa.Column('line_id', sa.Integer(), nullable=True),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('filename', sa.String(length=300), nullable=False),
    sa.Column('content_type', sa.String(length=120), nullable=False),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.Column('drive_file_id', sa.String(length=200), nullable=False),
    sa.Column('data', sa.LargeBinary(), nullable=True),
    sa.Column('taken_on', sa.Date(), nullable=True),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('summary', sa.JSON(), nullable=True),
    sa.Column('shared', sa.Boolean(), nullable=False),
    sa.Column('uploaded_by', sa.String(length=254), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['booking_id'], ['bookings.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['line_id'], ['booking_lines.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('proofs', schema=None) as batch_op:
        batch_op.create_index('ix_proofs_booking', ['booking_id'], unique=False)

    with op.batch_alter_table('booking_lines', schema=None) as batch_op:
        batch_op.add_column(sa.Column('plan', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('confirmed_plan', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('loaded_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('loaded_by', sa.String(length=254), server_default='', nullable=False))
        batch_op.add_column(sa.Column('loaded_signature', sa.String(length=200), server_default='', nullable=False))
        batch_op.add_column(sa.Column('load_files', sa.JSON(), nullable=True))

    with op.batch_alter_table('bookings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('plan_status', sa.String(length=20), server_default='none', nullable=False))
        batch_op.add_column(sa.Column('plan_note', sa.Text(), server_default='', nullable=False))
        batch_op.add_column(sa.Column('plan_confidence', sa.String(length=10), server_default='', nullable=False))
        batch_op.add_column(sa.Column('plan_override_reason', sa.String(length=500), server_default='', nullable=False))
        batch_op.add_column(sa.Column('plan_confirmed_by', sa.String(length=254), server_default='', nullable=False))
        batch_op.add_column(sa.Column('plan_confirmed_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('plan_client_approved_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('plan_client_note', sa.Text(), server_default='', nullable=False))
        batch_op.add_column(sa.Column('client_accepted_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('ready_alerted_on', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('live_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('renewal_prompted_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('wrapped_up_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('load_folder_id', sa.String(length=200), server_default='', nullable=False))

    with op.batch_alter_table('clients', schema=None) as batch_op:
        batch_op.add_column(sa.Column('preferred_channel', sa.String(length=20), server_default='', nullable=False))

    with op.batch_alter_table('creatives', schema=None) as batch_op:
        batch_op.add_column(sa.Column('client_approved_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('client_note', sa.Text(), server_default='', nullable=False))

    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.add_column(sa.Column('topic', sa.String(length=30), server_default='', nullable=False))

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('notify_prefs', sa.JSON(), nullable=True))



def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('notify_prefs')

    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.drop_column('topic')

    with op.batch_alter_table('creatives', schema=None) as batch_op:
        batch_op.drop_column('client_note')
        batch_op.drop_column('client_approved_at')

    with op.batch_alter_table('clients', schema=None) as batch_op:
        batch_op.drop_column('preferred_channel')

    with op.batch_alter_table('bookings', schema=None) as batch_op:
        batch_op.drop_column('load_folder_id')
        batch_op.drop_column('wrapped_up_at')
        batch_op.drop_column('renewal_prompted_at')
        batch_op.drop_column('live_at')
        batch_op.drop_column('ready_alerted_on')
        batch_op.drop_column('client_accepted_at')
        batch_op.drop_column('plan_client_note')
        batch_op.drop_column('plan_client_approved_at')
        batch_op.drop_column('plan_confirmed_at')
        batch_op.drop_column('plan_confirmed_by')
        batch_op.drop_column('plan_override_reason')
        batch_op.drop_column('plan_confidence')
        batch_op.drop_column('plan_note')
        batch_op.drop_column('plan_status')

    with op.batch_alter_table('booking_lines', schema=None) as batch_op:
        batch_op.drop_column('load_files')
        batch_op.drop_column('loaded_signature')
        batch_op.drop_column('loaded_by')
        batch_op.drop_column('loaded_at')
        batch_op.drop_column('confirmed_plan')
        batch_op.drop_column('plan')

    with op.batch_alter_table('proofs', schema=None) as batch_op:
        batch_op.drop_index('ix_proofs_booking')

    op.drop_table('proofs')
    op.drop_table('login_links')
