"""Phase 5: whether a person changed the agent's work (for the owner dashboard)

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-08 09:04:01.880935
"""
from alembic import op
import sqlalchemy as sa


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('bookings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('plan_drafted_sig', sa.String(length=40), server_default='', nullable=False))
        batch_op.add_column(sa.Column('plan_edited', sa.Boolean(), server_default=sa.false(), nullable=False))

    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.add_column(sa.Column('edited', sa.Boolean(), server_default=sa.false(), nullable=False))

    with op.batch_alter_table('proposals', schema=None) as batch_op:
        batch_op.add_column(sa.Column('edited', sa.Boolean(), server_default=sa.false(), nullable=False))



def downgrade():
    with op.batch_alter_table('proposals', schema=None) as batch_op:
        batch_op.drop_column('edited')

    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.drop_column('edited')

    with op.batch_alter_table('bookings', schema=None) as batch_op:
        batch_op.drop_column('plan_edited')
        batch_op.drop_column('plan_drafted_sig')

