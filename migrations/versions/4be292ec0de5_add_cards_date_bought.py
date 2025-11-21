from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "4be292ec0de5"
down_revision = "617a2fac0167"
branch_labels = None
depends_on = None

def upgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)
    cols = {c["name"] for c in insp.get_columns("cards")}

    # Only add if missing (avoid duplicate column error)
    if "date_bought" not in cols:
        with op.batch_alter_table("cards", schema=None) as batch:
            batch.add_column(sa.Column("date_bought", sa.Date(), nullable=True))

def downgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)
    cols = {c["name"] for c in insp.get_columns("cards")}

    if "date_bought" in cols:
        with op.batch_alter_table("cards", schema=None) as batch:
            batch.drop_column("date_bought")
