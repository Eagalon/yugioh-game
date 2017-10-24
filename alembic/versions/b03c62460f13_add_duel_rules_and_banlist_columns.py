"""add duel rules and banlist columns

Revision ID: b03c62460f13
Revises: 
Create Date: 2017-10-24 13:14:27.010614

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b03c62460f13'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('accounts', sa.Column('banlist', sa.String(length=50), nullable=False, server_default='tcg'))
    op.add_column('accounts', sa.Column('duel_rules', sa.Integer(), nullable=False, server_default='0'))
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('accounts', 'duel_rules')
    op.drop_column('accounts', 'banlist')
    # ### end Alembic commands ###
