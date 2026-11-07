"""Phase 3 creative

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-07 20:04:10.159046
"""
from alembic import op
import sqlalchemy as sa


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('creative_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('started_by', sa.String(length=254), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=False),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.Column('results', sa.JSON(), nullable=False),
    sa.Column('summary', sa.JSON(), nullable=False),
    sa.Column('usd', sa.Float(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('creative_cases',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('filename', sa.String(length=300), nullable=False),
    sa.Column('content_type', sa.String(length=120), nullable=False),
    sa.Column('data', sa.LargeBinary(), nullable=False),
    sa.Column('spec_id', sa.Integer(), nullable=False),
    sa.Column('ad_seconds', sa.Integer(), nullable=False),
    sa.Column('client_name', sa.String(length=200), nullable=False),
    sa.Column('expected', sa.JSON(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.String(length=254), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['spec_id'], ['specs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('creatives',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('booking_id', sa.Integer(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('filename', sa.String(length=300), nullable=False),
    sa.Column('content_type', sa.String(length=120), nullable=False),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('drive_file_id', sa.String(length=200), nullable=False),
    sa.Column('data', sa.LargeBinary(), nullable=True),
    sa.Column('line_ids', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('probe', sa.JSON(), nullable=True),
    sa.Column('results', sa.JSON(), nullable=True),
    sa.Column('team_summary', sa.Text(), nullable=False),
    sa.Column('error', sa.Text(), nullable=False),
    sa.Column('message_id', sa.Integer(), nullable=True),
    sa.Column('received_at', sa.DateTime(), nullable=False),
    sa.Column('checked_at', sa.DateTime(), nullable=True),
    sa.Column('decided_by', sa.String(length=254), nullable=False),
    sa.Column('decided_at', sa.DateTime(), nullable=True),
    sa.Column('decision_note', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['booking_id'], ['bookings.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('creatives', schema=None) as batch_op:
        batch_op.create_index('ix_creatives_booking', ['booking_id'], unique=False)
        batch_op.create_index('ix_creatives_drive', ['drive_file_id'], unique=False)

    op.create_table('creative_frames',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('creative_id', sa.Integer(), nullable=False),
    sa.Column('at_seconds', sa.Float(), nullable=False),
    sa.Column('jpeg', sa.LargeBinary(), nullable=False),
    sa.ForeignKeyConstraint(['creative_id'], ['creatives.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('ai_calls', schema=None) as batch_op:
        batch_op.add_column(sa.Column('booking_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('creative_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_ai_calls_creative', 'creatives', ['creative_id'], ['id'], ondelete='SET NULL')
        batch_op.create_foreign_key('fk_ai_calls_booking', 'bookings', ['booking_id'], ['id'])

    with op.batch_alter_table('bookings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('drive_folder_id', sa.String(length=200), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('upload_token', sa.String(length=64), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('creative_deadline', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('creative_requested_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('creative_reminded_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('creative_alerted_on', sa.Date(), nullable=True))



def downgrade():
    with op.batch_alter_table('bookings', schema=None) as batch_op:
        batch_op.drop_column('creative_alerted_on')
        batch_op.drop_column('creative_reminded_at')
        batch_op.drop_column('creative_requested_at')
        batch_op.drop_column('creative_deadline')
        batch_op.drop_column('upload_token')
        batch_op.drop_column('drive_folder_id')

    with op.batch_alter_table('ai_calls', schema=None) as batch_op:
        batch_op.drop_constraint('fk_ai_calls_creative', type_='foreignkey')
        batch_op.drop_constraint('fk_ai_calls_booking', type_='foreignkey')
        batch_op.drop_column('creative_id')
        batch_op.drop_column('booking_id')

    op.drop_table('creative_frames')
    with op.batch_alter_table('creatives', schema=None) as batch_op:
        batch_op.drop_index('ix_creatives_drive')
        batch_op.drop_index('ix_creatives_booking')

    op.drop_table('creatives')
    op.drop_table('creative_cases')
    op.drop_table('creative_runs')
