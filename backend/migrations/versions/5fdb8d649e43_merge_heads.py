"""merge heads

Revision ID: 5fdb8d649e43
Revises: 0026_add_friend_card_requests, 8e503d638cb7, add_performance_indexes
Create Date: 2026-02-06 13:23:21.179064

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5fdb8d649e43'
down_revision = ('0026_add_friend_card_requests', '8e503d638cb7', 'add_performance_indexes')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
