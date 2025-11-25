"""Add oracle_roles table and primary flag on card_roles."""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "c3e4a7b2f1ac"
down_revision = "a19b8d6c4f5e"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "card_roles",
        sa.Column("primary", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )
    op.create_table(
        "oracle_roles",
        sa.Column("oracle_id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("type_line", sa.Text(), nullable=True),
        sa.Column("primary_role", sa.String(length=128), nullable=True),
        sa.Column("roles", sa.JSON(), nullable=True),
        sa.Column("subroles", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now()),
    )


def downgrade():
    op.drop_table("oracle_roles")
    op.drop_column("card_roles", "primary")
