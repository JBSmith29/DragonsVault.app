from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "65292d83eb59"
down_revision = "4be292ec0de5"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "wishlist_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("card_id", sa.Integer, sa.ForeignKey("cards.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("scryfall_id", sa.String(64), nullable=True, index=True),
        sa.Column("name", sa.String(200), nullable=False, index=True),
        sa.Column("requested_qty", sa.Integer, nullable=False, server_default="0"),
        sa.Column("missing_qty", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="open", index=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )

def downgrade():
    op.drop_table("wishlist_items")
