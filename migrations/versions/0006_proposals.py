"""Proposals to clients (options, negotiation versions), screen rates and the purchase order

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-08 12:30:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('screens', schema=None) as batch_op:
        batch_op.add_column(sa.Column('rate_week', sa.Float(), nullable=True))

    with op.batch_alter_table('bookings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('agreed_amount', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('currency', sa.String(length=3), server_default='', nullable=False))
        batch_op.add_column(sa.Column('po_number', sa.String(length=80), server_default='', nullable=False))
        batch_op.add_column(sa.Column('po_amount', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('po_received_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('po_received_by', sa.String(length=254), server_default='', nullable=False))
        batch_op.add_column(sa.Column('no_po_reason', sa.String(length=500), server_default='', nullable=False))

    op.create_table(
        'quotes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('ref', sa.String(length=20), nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('request_id', sa.Integer(), nullable=True),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('budget', sa.Float(), nullable=True),
        sa.Column('valid_until', sa.Date(), nullable=True),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('internal_note', sa.Text(), nullable=False),
        sa.Column('client_note', sa.Text(), nullable=False),
        sa.Column('accepted_option_id', sa.Integer(), nullable=True),
        sa.Column('accepted_at', sa.DateTime(), nullable=True),
        sa.Column('accepted_by', sa.String(length=254), nullable=False),
        sa.Column('booking_id', sa.Integer(), nullable=True),
        sa.Column('created_by', sa.String(length=254), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('sent_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['booking_id'], ['bookings.id']),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id']),
        sa.ForeignKeyConstraint(['request_id'], ['proposals.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_quotes_ref', 'quotes', ['ref'])
    op.create_index('ix_quotes_status', 'quotes', ['status'])
    op.create_index('ix_quotes_client', 'quotes', ['client_id'])

    op.create_table(
        'quote_options',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('quote_id', sa.Integer(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('discount_pct', sa.Float(), nullable=False),
        sa.Column('value_adds', sa.Text(), nullable=False),
        sa.Column('agreed_total', sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(['quote_id'], ['quotes.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'quote_lines',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('option_id', sa.Integer(), nullable=False),
        sa.Column('screen_id', sa.Integer(), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=False),
        sa.Column('start_hour', sa.Integer(), nullable=True),
        sa.Column('end_hour', sa.Integer(), nullable=True),
        sa.Column('ad_seconds', sa.Integer(), nullable=False),
        sa.Column('plays_per_hour', sa.Float(), nullable=False),
        sa.Column('exclusive', sa.Boolean(), nullable=False),
        sa.Column('bonus', sa.Boolean(), nullable=False),
        sa.Column('price', sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(['option_id'], ['quote_options.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['screen_id'], ['screens.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'quote_versions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('quote_id', sa.Integer(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('snapshot', sa.JSON(), nullable=False),
        sa.Column('note', sa.Text(), nullable=False),
        sa.Column('sent_by', sa.String(length=254), nullable=False),
        sa.Column('sent_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['quote_id'], ['quotes.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    op.drop_table('quote_versions')
    op.drop_table('quote_lines')
    op.drop_table('quote_options')
    op.drop_index('ix_quotes_client', table_name='quotes')
    op.drop_index('ix_quotes_status', table_name='quotes')
    op.drop_index('ix_quotes_ref', table_name='quotes')
    op.drop_table('quotes')
    with op.batch_alter_table('bookings', schema=None) as batch_op:
        for c in ('no_po_reason', 'po_received_by', 'po_received_at', 'po_amount', 'po_number', 'currency',
                  'agreed_amount'):
            batch_op.drop_column(c)
    with op.batch_alter_table('screens', schema=None) as batch_op:
        batch_op.drop_column('rate_week')
