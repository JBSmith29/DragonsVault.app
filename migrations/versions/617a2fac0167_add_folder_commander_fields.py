from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "617a2fac0167"
down_revision = "0d7c15882410"
branch_labels = None
depends_on = None

def upgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)
    cols = {c["name"] for c in insp.get_columns("folder")}

    # Use batch_alter_table for SQLite safety, but only add if missing
    with op.batch_alter_table("folder", schema=None) as batch:
        if "commander_oracle_id" not in cols:
            batch.add_column(sa.Column("commander_oracle_id", sa.String(length=64)))
        if "commander_name" not in cols:
            batch.add_column(sa.Column("commander_name", sa.String(length=255)))

def downgrade():
    # Guarded drops (some SQLite backends rebuild the table)
    conn = op.get_bind()
    insp = sa.inspect(conn)
    cols = {c["name"] for c in insp.get_columns("folder")}
    with op.batch_alter_table("folder", schema=None) as batch:
        if "commander_name" in cols:
            batch.drop_column("commander_name")
        if "commander_oracle_id" in cols:
            batch.drop_column("commander_oracle_id")
