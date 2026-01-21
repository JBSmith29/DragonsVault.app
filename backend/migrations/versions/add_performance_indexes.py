"""Add performance indexes

Revision ID: add_performance_indexes
Revises: 
Create Date: 2024-01-02 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_performance_indexes'
down_revision = None
branch_labels = None
depends_on = "0024_add_game_pods"


def upgrade():
    """Add missing indexes for performance optimization."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())
    game_sessions_columns = set()
    if "game_sessions" in table_names:
        game_sessions_columns = {col["name"] for col in inspector.get_columns("game_sessions")}
    
    # Cards table indexes
    op.create_index('ix_cards_folder_id_name', 'cards', ['folder_id', 'name'], if_not_exists=True)
    op.create_index('ix_cards_oracle_id', 'cards', ['oracle_id'], if_not_exists=True)
    op.create_index(
        'ix_cards_set_code_collector_number',
        'cards',
        ['set_code', 'collector_number'],
        if_not_exists=True,
    )
    op.create_index('ix_cards_name_lower', 'cards', [sa.text('LOWER(name)')], if_not_exists=True)
    op.create_index('ix_cards_quantity', 'cards', ['quantity'], if_not_exists=True)
    op.create_index('ix_cards_updated_at', 'cards', ['updated_at'], if_not_exists=True)
    
    # Folders table indexes
    op.create_index('ix_folders_owner_user_id', 'folder', ['owner_user_id'], if_not_exists=True)
    op.create_index('ix_folders_name_lower', 'folder', [sa.text('LOWER(name)')], if_not_exists=True)
    op.create_index('ix_folders_category', 'folder', ['category'], if_not_exists=True)
    op.create_index('ix_folders_is_proxy', 'folder', ['is_proxy'], if_not_exists=True)
    
    # Users table indexes
    op.create_index('ix_users_email_lower', 'users', [sa.text('LOWER(email)')], if_not_exists=True)
    op.create_index('ix_users_username_lower', 'users', [sa.text('LOWER(username)')], if_not_exists=True)
    
    # Game-related indexes
    op.create_index('ix_game_sessions_created_at', 'game_sessions', ['created_at'], if_not_exists=True)
    if "pod_id" in game_sessions_columns:
        op.create_index('ix_game_sessions_pod_id', 'game_sessions', ['pod_id'], if_not_exists=True)
    
    # Wishlist indexes
    op.create_index('ix_wishlist_items_name', 'wishlist_items', ['name'], if_not_exists=True)
    op.create_index('ix_wishlist_items_status', 'wishlist_items', ['status'], if_not_exists=True)


def downgrade():
    """Remove performance indexes."""
    
    # Drop indexes in reverse order
    op.drop_index('ix_wishlist_items_status')
    op.drop_index('ix_wishlist_items_name')
    
    op.drop_index('ix_game_sessions_pod_id')
    op.drop_index('ix_game_sessions_created_at')
    
    op.drop_index('ix_users_username_lower')
    op.drop_index('ix_users_email_lower')
    
    op.drop_index('ix_folders_is_proxy')
    op.drop_index('ix_folders_category')
    op.drop_index('ix_folders_name_lower')
    op.drop_index('ix_folders_owner_user_id')
    
    op.drop_index('ix_cards_updated_at')
    op.drop_index('ix_cards_quantity')
    op.drop_index('ix_cards_name_lower')
    op.drop_index('ix_cards_set_code_collector_number')
    op.drop_index('ix_cards_oracle_id')
    op.drop_index('ix_cards_folder_id_name')
