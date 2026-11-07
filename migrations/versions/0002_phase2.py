"""Phase 2: proposals, messages, attachments, AI cost ledger, accuracy test

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-07 19:41:56.517778
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('eval_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('model', sa.String(length=80), nullable=False),
    sa.Column('started_by', sa.String(length=254), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=False),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.Column('results', sa.JSON(), nullable=False),
    sa.Column('summary', sa.JSON(), nullable=False),
    sa.Column('usd', sa.Float(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('booking_documents',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('booking_id', sa.Integer(), nullable=False),
    sa.Column('filename', sa.String(length=300), nullable=False),
    sa.Column('content_type', sa.String(length=120), nullable=False),
    sa.Column('data', sa.LargeBinary(), nullable=False),
    sa.Column('uploaded_by', sa.String(length=254), nullable=False),
    sa.Column('uploaded_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['booking_id'], ['bookings.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('proposals',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('ref', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('sender', sa.String(length=254), nullable=False),
    sa.Column('sender_name', sa.String(length=200), nullable=False),
    sa.Column('subject', sa.String(length=300), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=True),
    sa.Column('language', sa.String(length=5), nullable=False),
    sa.Column('reading', sa.JSON(), nullable=True),
    sa.Column('draft', sa.JSON(), nullable=True),
    sa.Column('checks', sa.JSON(), nullable=True),
    sa.Column('ready', sa.Boolean(), nullable=False),
    sa.Column('duplicate_of_id', sa.Integer(), nullable=True),
    sa.Column('booking_id', sa.Integer(), nullable=True),
    sa.Column('auto_held', sa.Boolean(), nullable=False),
    sa.Column('error', sa.Text(), nullable=False),
    sa.Column('read_count', sa.Integer(), nullable=False),
    sa.Column('last_read_at', sa.DateTime(), nullable=True),
    sa.Column('decided_by', sa.String(length=254), nullable=False),
    sa.Column('decided_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['booking_id'], ['bookings.id'], ),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ),
    sa.ForeignKeyConstraint(['duplicate_of_id'], ['proposals.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('proposals', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_proposals_created_at'), ['created_at'], unique=False)
        batch_op.create_index('ix_proposals_sender', ['sender'], unique=False)
        batch_op.create_index('ix_proposals_status', ['status'], unique=False)

    op.create_table('ai_calls',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('at', sa.DateTime(), nullable=False),
    sa.Column('purpose', sa.String(length=40), nullable=False),
    sa.Column('model', sa.String(length=80), nullable=False),
    sa.Column('input_tokens', sa.Integer(), nullable=False),
    sa.Column('output_tokens', sa.Integer(), nullable=False),
    sa.Column('cache_write_tokens', sa.Integer(), nullable=False),
    sa.Column('cache_read_tokens', sa.Integer(), nullable=False),
    sa.Column('usd', sa.Float(), nullable=False),
    sa.Column('actor', sa.String(length=254), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=True),
    sa.Column('proposal_id', sa.Integer(), nullable=True),
    sa.Column('eval_run_id', sa.Integer(), nullable=True),
    sa.Column('ok', sa.Boolean(), nullable=False),
    sa.Column('stop_reason', sa.String(length=40), nullable=False),
    sa.Column('error', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ),
    sa.ForeignKeyConstraint(['eval_run_id'], ['eval_runs.id'], ),
    sa.ForeignKeyConstraint(['proposal_id'], ['proposals.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('ai_calls', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ai_calls_at'), ['at'], unique=False)

    op.create_table('eval_cases',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('sender', sa.String(length=254), nullable=False),
    sa.Column('subject', sa.String(length=300), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('received_on', sa.Date(), nullable=True),
    sa.Column('expected', sa.JSON(), nullable=False),
    sa.Column('source_proposal_id', sa.Integer(), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.String(length=254), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['source_proposal_id'], ['proposals.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('messages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('proposal_id', sa.Integer(), nullable=True),
    sa.Column('booking_id', sa.Integer(), nullable=True),
    sa.Column('direction', sa.String(length=3), nullable=False),
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('kind', sa.String(length=30), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('sender', sa.String(length=254), nullable=False),
    sa.Column('recipient', sa.String(length=254), nullable=False),
    sa.Column('subject', sa.String(length=300), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('language', sa.String(length=5), nullable=False),
    sa.Column('external_id', sa.String(length=300), nullable=False),
    sa.Column('in_reply_to', sa.String(length=300), nullable=False),
    sa.Column('approved_by', sa.String(length=254), nullable=False),
    sa.Column('error', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('sent_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['booking_id'], ['bookings.id'], ),
    sa.ForeignKeyConstraint(['proposal_id'], ['proposals.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.create_index('ix_messages_external', ['external_id'], unique=False)
        batch_op.create_index('ix_messages_status', ['status'], unique=False)

    op.create_table('attachments',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('message_id', sa.Integer(), nullable=True),
    sa.Column('eval_case_id', sa.Integer(), nullable=True),
    sa.Column('filename', sa.String(length=300), nullable=False),
    sa.Column('content_type', sa.String(length=120), nullable=False),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.Column('data', sa.LargeBinary(), nullable=True),
    sa.Column('external_ref', sa.String(length=300), nullable=False),
    sa.Column('text', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['eval_case_id'], ['eval_cases.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade():
    op.drop_table('attachments')
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.drop_index('ix_messages_status')
        batch_op.drop_index('ix_messages_external')

    op.drop_table('messages')
    op.drop_table('eval_cases')
    with op.batch_alter_table('ai_calls', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ai_calls_at'))

    op.drop_table('ai_calls')
    with op.batch_alter_table('proposals', schema=None) as batch_op:
        batch_op.drop_index('ix_proposals_status')
        batch_op.drop_index('ix_proposals_sender')
        batch_op.drop_index(batch_op.f('ix_proposals_created_at'))

    op.drop_table('proposals')
    op.drop_table('booking_documents')
    op.drop_table('eval_runs')
