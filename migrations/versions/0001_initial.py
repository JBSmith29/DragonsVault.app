"""Initial baseline migration for DragonsVault."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Users ----------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=80), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("display_name", sa.String(length=120), nullable=True),
        sa.Column("api_token_hash", sa.String(length=64), nullable=True),
        sa.Column("api_token_hint", sa.String(length=12), nullable=True),
        sa.Column("api_token_created_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("is_admin IN (false,true)", name="ck_users_is_admin_bool"),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("api_token_hash"),
        sa.UniqueConstraint("username"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_index("ix_users_last_seen_at", "users", ["last_seen_at"], unique=False)
    op.create_index("ix_users_archived_at", "users", ["archived_at"], unique=False)

    # Roles ---------------------------------------------------------------
    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.UniqueConstraint("key"),
    )
    op.create_index("ix_roles_key", "roles", ["key"], unique=True)
    op.create_index("ix_roles_created_at", "roles", ["created_at"], unique=False)
    op.create_index("ix_roles_updated_at", "roles", ["updated_at"], unique=False)

    op.create_table(
        "sub_roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("roles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.UniqueConstraint("role_id", "key", name="uq_sub_roles_role_key"),
    )
    op.create_index("ix_sub_roles_role_id", "sub_roles", ["role_id"])
    op.create_index("ix_sub_roles_created_at", "sub_roles", ["created_at"], unique=False)
    op.create_index("ix_sub_roles_updated_at", "sub_roles", ["updated_at"], unique=False)

    # Folders -------------------------------------------------------------
    op.create_table(
        "folder",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=False, server_default=sa.text("'deck'")),
        sa.Column("commander_oracle_id", sa.String(length=128), nullable=True),
        sa.Column("commander_name", sa.String(length=200), nullable=True),
        sa.Column("deck_tag", sa.String(length=120), nullable=True),
        sa.Column("owner", sa.String(length=120), nullable=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_proxy", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("share_token_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "category in ('deck','collection','build')",
            name="ck_folder_category",
        ),
        sa.UniqueConstraint("owner_user_id", "name", name="uq_folder_owner_name"),
        sa.UniqueConstraint("share_token_hash", name="uq_folder_share_token_hash"),
    )
    op.create_index("ix_folder_name", "folder", ["name"], unique=False)
    op.create_index("ix_folder_category", "folder", ["category"], unique=False)
    op.create_index("ix_folder_deck_tag", "folder", ["deck_tag"], unique=False)
    op.create_index("ix_folder_owner", "folder", ["owner"], unique=False)
    op.create_index("ix_folder_owner_user_id", "folder", ["owner_user_id"], unique=False)
    op.create_index("ix_folder_is_proxy", "folder", ["is_proxy"], unique=False)
    op.create_index("ix_folder_is_public", "folder", ["is_public"], unique=False)
    op.create_index("ix_folder_commander_oracle_id", "folder", ["commander_oracle_id"], unique=False)
    op.create_index("ix_folder_created_at", "folder", ["created_at"], unique=False)
    op.create_index("ix_folder_updated_at", "folder", ["updated_at"], unique=False)
    op.create_index("ix_folder_archived_at", "folder", ["archived_at"], unique=False)
    op.create_index("ix_folder_share_token_hash", "folder", ["share_token_hash"], unique=True)

    # Cards ---------------------------------------------------------------
    op.create_table(
        "cards",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("set_code", sa.String(length=10), nullable=False),
        sa.Column("collector_number", sa.String(length=20), nullable=False),
        sa.Column("date_bought", sa.Date(), nullable=True),
        sa.Column("folder_id", sa.Integer(), sa.ForeignKey("folder.id", ondelete="CASCADE"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("oracle_id", sa.String(length=36), nullable=True),
        sa.Column("lang", sa.String(length=5), nullable=False, server_default=sa.text("'en'")),
        sa.Column("is_foil", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_proxy", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("type_line", sa.Text(), nullable=True),
        sa.Column("rarity", sa.String(length=16), nullable=True),
        sa.Column("color_identity_mask", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("quantity >= 0", name="ck_cards_quantity_nonneg"),
    )
    op.create_index("ix_cards_name", "cards", ["name"], unique=False)
    op.create_index("ix_cards_folder_id", "cards", ["folder_id"], unique=False)
    op.create_index("ix_cards_set_code", "cards", ["set_code"], unique=False)
    op.create_index("ix_cards_lang", "cards", ["lang"], unique=False)
    op.create_index("ix_cards_is_foil", "cards", ["is_foil"], unique=False)
    op.create_index("ix_cards_is_proxy", "cards", ["is_proxy"], unique=False)
    op.create_index("ix_cards_rarity", "cards", ["rarity"], unique=False)
    op.create_index("ix_cards_collector_number", "cards", ["collector_number"], unique=False)
    op.create_index("ix_cards_oracle_id", "cards", ["oracle_id"], unique=False)
    op.create_index("ix_cards_set_cn", "cards", ["set_code", "collector_number"], unique=False)
    op.create_index("ix_cards_folder_name", "cards", ["folder_id", "name"], unique=False)
    op.create_index("ix_cards_folder_set_cn", "cards", ["folder_id", "set_code", "collector_number"], unique=False)
    op.create_index("ix_cards_name_folder", "cards", ["name", "folder_id"], unique=False)
    op.create_index("ix_cards_created_at", "cards", ["created_at"], unique=False)
    op.create_index("ix_cards_updated_at", "cards", ["updated_at"], unique=False)
    op.create_index("ix_cards_archived_at", "cards", ["archived_at"], unique=False)
    op.create_index(
        "ix_cards_oracle_print",
        "cards",
        ["oracle_id", "set_code", "collector_number", "is_foil", "lang"],
        unique=False,
    )
    op.create_index(
        "uq_cards_print_per_folder",
        "cards",
        ["name", "folder_id", "set_code", "collector_number", "lang", "is_foil"],
        unique=True,
    )

    # SQLite FTS for card names and metadata
    conn = op.get_bind()
    if conn.dialect.name == "sqlite":
        conn.exec_driver_sql(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS cards_fts USING fts5(
              name, set_code, collector_number, lang, is_foil,
              content='cards', content_rowid='id'
            );
            """
        )
        conn.exec_driver_sql(
            """
            INSERT INTO cards_fts(rowid, name, set_code, collector_number, lang, is_foil)
              SELECT id, lower(name), set_code, collector_number, lang,
                     CASE WHEN is_foil THEN '1' ELSE '0' END
              FROM cards
              WHERE name IS NOT NULL
                AND NOT EXISTS (SELECT 1 FROM cards_fts WHERE rowid = cards.id);
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TRIGGER IF NOT EXISTS cards_ai AFTER INSERT ON cards BEGIN
              INSERT INTO cards_fts(rowid, name, set_code, collector_number, lang, is_foil)
              VALUES (new.id, lower(new.name), new.set_code, new.collector_number, new.lang,
                      CASE WHEN new.is_foil THEN '1' ELSE '0' END);
            END;
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TRIGGER IF NOT EXISTS cards_au AFTER UPDATE ON cards BEGIN
              UPDATE cards_fts
                 SET name = lower(new.name),
                     set_code = new.set_code,
                     collector_number = new.collector_number,
                     lang = new.lang,
                     is_foil = CASE WHEN new.is_foil THEN '1' ELSE '0' END
               WHERE rowid = new.id;
            END;
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TRIGGER IF NOT EXISTS cards_ad AFTER DELETE ON cards BEGIN
              DELETE FROM cards_fts WHERE rowid = old.id;
            END;
            """
        )

    op.create_table(
        "card_roles",
        sa.Column("card_id", sa.Integer(), sa.ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("primary", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "card_subroles",
        sa.Column("card_id", sa.Integer(), sa.ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("subrole_id", sa.Integer(), sa.ForeignKey("sub_roles.id", ondelete="CASCADE"), primary_key=True),
    )

    # Reference data ------------------------------------------------------
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

    # Logs ---------------------------------------------------------------
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"], unique=False)
    op.create_index("ix_audit_logs_updated_at", "audit_logs", ["updated_at"], unique=False)
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"], unique=False)

    # Sharing / cache -----------------------------------------------------
    op.create_table(
        "folder_share",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("folder_id", sa.Integer(), sa.ForeignKey("folder.id", ondelete="CASCADE"), nullable=False),
        sa.Column("shared_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_folder_share_folder_id", "folder_share", ["folder_id"], unique=False)
    op.create_index("ix_folder_share_shared_user_id", "folder_share", ["shared_user_id"], unique=False)
    op.create_index("ix_folder_share_created_at", "folder_share", ["created_at"], unique=False)

    op.create_table(
        "commander_bracket_cache",
        sa.Column("folder_id", sa.Integer(), sa.ForeignKey("folder.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("cache_epoch", sa.Integer(), nullable=False),
        sa.Column("card_signature", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_commander_bracket_cache_signature",
        "commander_bracket_cache",
        ["card_signature"],
        unique=False,
    )
    op.create_index(
        "ix_commander_bracket_cache_epoch",
        "commander_bracket_cache",
        ["cache_epoch"],
        unique=False,
    )

    # Site requests -------------------------------------------------------
    op.create_table(
        "site_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("request_type", sa.String(length=20), nullable=False, server_default=sa.text("'bug'")),
        sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'not_started'")),
        sa.Column("requester_name", sa.String(length=120), nullable=True),
        sa.Column("requester_email", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "request_type in ('bug','feature')",
            name="ck_site_requests_request_type",
        ),
        sa.CheckConstraint(
            "status in ('not_started','working','completed')",
            name="ck_site_requests_status",
        ),
    )
    op.create_index("ix_site_requests_request_type", "site_requests", ["request_type"], unique=False)
    op.create_index("ix_site_requests_status", "site_requests", ["status"], unique=False)
    op.create_index("ix_site_requests_created_at", "site_requests", ["created_at"], unique=False)

    # Wishlist ------------------------------------------------------------
    op.create_table(
        "wishlist_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("card_id", sa.Integer(), sa.ForeignKey("cards.id", ondelete="CASCADE"), nullable=True),
        sa.Column("oracle_id", sa.String(length=64), nullable=True),
        sa.Column("scryfall_id", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("requested_qty", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("missing_qty", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.String(length=16), nullable=False, server_default=sa.text("'open'")),
        sa.Column("source_folders", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.CheckConstraint(
            "status in ('open','to_fetch','ordered','acquired','removed')",
            name="ck_wishlist_items_status",
        ),
        sa.CheckConstraint("requested_qty >= 0", name="ck_wishlist_items_requested_qty_nonneg"),
        sa.CheckConstraint("missing_qty >= 0", name="ck_wishlist_items_missing_qty_nonneg"),
    )
    op.create_index("ix_wishlist_items_card_id", "wishlist_items", ["card_id"], unique=False)
    op.create_index("ix_wishlist_items_oracle_id", "wishlist_items", ["oracle_id"], unique=False)
    op.create_index("ix_wishlist_items_scryfall_id", "wishlist_items", ["scryfall_id"], unique=False)
    op.create_index("ix_wishlist_items_name", "wishlist_items", ["name"], unique=False)
    op.create_index("ix_wishlist_items_status", "wishlist_items", ["status"], unique=False)
    op.create_index("ix_wishlist_items_oracle_status", "wishlist_items", ["oracle_id", "status"], unique=False)
    op.create_index("ix_wishlist_items_name_status", "wishlist_items", ["name", "status"], unique=False)
    op.create_index("ix_wishlist_items_created_at", "wishlist_items", ["created_at"], unique=False)
    op.create_index("ix_wishlist_items_updated_at", "wishlist_items", ["updated_at"], unique=False)


def downgrade() -> None:
    # Drop tables in reverse dependency order
    op.drop_index("ix_wishlist_items_updated_at", table_name="wishlist_items")
    op.drop_index("ix_wishlist_items_created_at", table_name="wishlist_items")
    op.drop_index("ix_wishlist_items_status", table_name="wishlist_items")
    op.drop_index("ix_wishlist_items_name_status", table_name="wishlist_items")
    op.drop_index("ix_wishlist_items_oracle_status", table_name="wishlist_items")
    op.drop_index("ix_wishlist_items_name", table_name="wishlist_items")
    op.drop_index("ix_wishlist_items_scryfall_id", table_name="wishlist_items")
    op.drop_index("ix_wishlist_items_oracle_id", table_name="wishlist_items")
    op.drop_index("ix_wishlist_items_card_id", table_name="wishlist_items")
    op.drop_table("wishlist_items")

    op.drop_index("ix_site_requests_created_at", table_name="site_requests")
    op.drop_index("ix_site_requests_status", table_name="site_requests")
    op.drop_index("ix_site_requests_request_type", table_name="site_requests")
    op.drop_table("site_requests")

    op.drop_index("ix_commander_bracket_cache_epoch", table_name="commander_bracket_cache")
    op.drop_index("ix_commander_bracket_cache_signature", table_name="commander_bracket_cache")
    op.drop_table("commander_bracket_cache")

    op.drop_index("ix_folder_share_created_at", table_name="folder_share")
    op.drop_index("ix_folder_share_shared_user_id", table_name="folder_share")
    op.drop_index("ix_folder_share_folder_id", table_name="folder_share")
    op.drop_table("folder_share")

    op.drop_index("ix_audit_logs_user_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_updated_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_table("oracle_roles")

    op.drop_table("card_subroles")
    op.drop_table("card_roles")

    op.drop_index("uq_cards_print_per_folder", table_name="cards")
    op.drop_index("ix_cards_oracle_print", table_name="cards")
    op.drop_index("ix_cards_archived_at", table_name="cards")
    op.drop_index("ix_cards_updated_at", table_name="cards")
    op.drop_index("ix_cards_created_at", table_name="cards")
    op.drop_index("ix_cards_name_folder", table_name="cards")
    op.drop_index("ix_cards_folder_set_cn", table_name="cards")
    op.drop_index("ix_cards_folder_name", table_name="cards")
    op.drop_index("ix_cards_set_cn", table_name="cards")
    op.drop_index("ix_cards_oracle_id", table_name="cards")
    op.drop_index("ix_cards_collector_number", table_name="cards")
    op.drop_index("ix_cards_rarity", table_name="cards")
    op.drop_index("ix_cards_is_proxy", table_name="cards")
    op.drop_index("ix_cards_is_foil", table_name="cards")
    op.drop_index("ix_cards_lang", table_name="cards")
    op.drop_index("ix_cards_set_code", table_name="cards")
    op.drop_index("ix_cards_folder_id", table_name="cards")
    op.drop_index("ix_cards_name", table_name="cards")
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TABLE IF EXISTS cards_fts;")
        op.execute("DROP TRIGGER IF EXISTS cards_ai;")
        op.execute("DROP TRIGGER IF EXISTS cards_au;")
        op.execute("DROP TRIGGER IF EXISTS cards_ad;")
    op.drop_table("cards")

    op.drop_index("ix_folder_share_token_hash", table_name="folder")
    op.drop_index("ix_folder_archived_at", table_name="folder")
    op.drop_index("ix_folder_updated_at", table_name="folder")
    op.drop_index("ix_folder_created_at", table_name="folder")
    op.drop_index("ix_folder_commander_oracle_id", table_name="folder")
    op.drop_index("ix_folder_is_public", table_name="folder")
    op.drop_index("ix_folder_is_proxy", table_name="folder")
    op.drop_index("ix_folder_owner_user_id", table_name="folder")
    op.drop_index("ix_folder_owner", table_name="folder")
    op.drop_index("ix_folder_deck_tag", table_name="folder")
    op.drop_index("ix_folder_category", table_name="folder")
    op.drop_index("ix_folder_name", table_name="folder")
    op.drop_table("folder")

    op.drop_index("ix_sub_roles_updated_at", table_name="sub_roles")
    op.drop_index("ix_sub_roles_created_at", table_name="sub_roles")
    op.drop_index("ix_sub_roles_role_id", table_name="sub_roles")
    op.drop_table("sub_roles")

    op.drop_index("ix_roles_updated_at", table_name="roles")
    op.drop_index("ix_roles_created_at", table_name="roles")
    op.drop_index("ix_roles_key", table_name="roles")
    op.drop_table("roles")

    op.drop_index("ix_users_username", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_archived_at", table_name="users")
    op.drop_index("ix_users_last_seen_at", table_name="users")
    op.drop_table("users")
