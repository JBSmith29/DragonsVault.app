"""empty message

Revision ID: 9f5d92a01961
Revises: 20251018_add_proxy_decks_and_owner, 20251114_add_usernames
Create Date: 2025-11-14 19:40:26.170853

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9f5d92a01961'
down_revision = ('20251018_add_proxy_decks_and_owner', '20251114_add_usernames')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
