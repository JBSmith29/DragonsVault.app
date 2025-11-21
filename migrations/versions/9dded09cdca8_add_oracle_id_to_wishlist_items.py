from alembic import op
import sqlalchemy as sa

revision = "9dded09cdca8"
down_revision = "65292d83eb59"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("wishlist_items", sa.Column("oracle_id", sa.String(length=64), nullable=True))

def downgrade():
    op.drop_column("wishlist_items", "oracle_id")
