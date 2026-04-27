# Database Schema Documentation

## Overview

DragonsVault uses PostgreSQL (with SQLite fallback) and follows a domain-driven design with the following core entities:

- **Users**: Authentication and user management
- **Folders**: Decks and collections
- **Cards**: Individual card instances within folders
- **Games**: Game tracking with pods, sessions, and players
- **Tags & Metadata**: Oracle tags, deck tags, and commander brackets

## Core Tables

### Users Domain

#### `users`
Primary user accounts and authentication.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | User ID |
| email | VARCHAR(255) | UNIQUE, NOT NULL | User email |
| username | VARCHAR(80) | UNIQUE, NOT NULL | Username |
| password_hash | VARCHAR(255) | NOT NULL | Hashed password |
| is_admin | BOOLEAN | DEFAULT FALSE | Admin flag |
| display_name | VARCHAR(120) | NULLABLE | Display name |
| api_token_hash | VARCHAR(64) | UNIQUE, NULLABLE | Hashed API token |
| api_token_hint | VARCHAR(12) | NULLABLE | Token hint (first 8 chars) |
| api_token_created_at | DATETIME | NULLABLE | Token creation timestamp |
| created_at | DATETIME | NOT NULL | Account creation |
| updated_at | DATETIME | NOT NULL | Last update |
| last_login_at | DATETIME | NULLABLE | Last login |
| last_seen_at | DATETIME | NULLABLE, INDEXED | Last activity |
| archived_at | DATETIME | NULLABLE, INDEXED | Soft delete timestamp |
| pw_reset_token_hash | VARCHAR(64) | UNIQUE, NULLABLE | Password reset token |
| pw_reset_token_expires_at | DATETIME | NULLABLE | Reset token expiry |

**Indexes:**
- `ix_users_email` (email)
- `ix_users_username` (username)
- `ix_users_last_seen_at` (last_seen_at)
- `ix_users_archived_at` (archived_at)
- `uq_users_api_token_hash` (api_token_hash)
- `uq_users_pw_reset_token_hash` (pw_reset_token_hash)

#### `user_settings`
User preferences and configuration.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Setting ID |
| user_id | INTEGER | FK users.id, NOT NULL | User reference |
| key | VARCHAR(120) | NOT NULL | Setting key |
| value | TEXT | NULLABLE | Setting value (JSON) |
| created_at | DATETIME | NOT NULL | Creation timestamp |
| updated_at | DATETIME | NOT NULL | Last update |

**Constraints:**
- `uq_user_settings_user_key` (user_id, key)

#### `user_follow`
User following relationships.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Follow ID |
| follower_user_id | INTEGER | FK users.id, NOT NULL | Follower |
| followed_user_id | INTEGER | FK users.id, NOT NULL | Followed user |
| created_at | DATETIME | NOT NULL | Follow timestamp |

**Constraints:**
- `uq_user_follow_pair` (follower_user_id, followed_user_id)

#### `user_friends`
Mutual friend relationships.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Friendship ID |
| user_id | INTEGER | FK users.id, NOT NULL | User 1 |
| friend_user_id | INTEGER | FK users.id, NOT NULL | User 2 |
| status | VARCHAR(20) | NOT NULL | pending/accepted/blocked |
| created_at | DATETIME | NOT NULL | Request timestamp |
| updated_at | DATETIME | NOT NULL | Status change |

**Constraints:**
- `uq_user_friends_pair` (user_id, friend_user_id)
- `ck_user_friends_status` (status IN ('pending', 'accepted', 'blocked'))

### Folders & Cards Domain

#### `folder`
Decks and collections container.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Folder ID |
| name | VARCHAR(120) | NOT NULL, INDEXED | Folder name |
| category | VARCHAR(20) | NOT NULL, INDEXED | deck/collection |
| commander_oracle_id | VARCHAR(128) | NULLABLE, INDEXED | Commander oracle ID |
| commander_name | VARCHAR(200) | NULLABLE | Commander name |
| deck_tag | VARCHAR(120) | NULLABLE, INDEXED | Legacy deck tag |
| owner | VARCHAR(120) | NULLABLE, INDEXED | Legacy owner field |
| owner_user_id | INTEGER | FK users.id, NULLABLE, INDEXED | Owner reference |
| is_proxy | BOOLEAN | DEFAULT FALSE, INDEXED | Proxy deck flag |
| notes | TEXT | NULLABLE | Deck notes |
| sleeve_color | VARCHAR(64) | NULLABLE | Sleeve color |
| is_public | BOOLEAN | DEFAULT FALSE, INDEXED | Public visibility |
| share_token_hash | VARCHAR(64) | UNIQUE, NULLABLE | Share token |
| created_at | DATETIME | NOT NULL, INDEXED | Creation timestamp |
| updated_at | DATETIME | NOT NULL, INDEXED | Last update |
| archived_at | DATETIME | NULLABLE, INDEXED | Soft delete |

**Constraints:**
- `uq_folder_owner_name` (owner_user_id, name)
- `ck_folder_category` (category IN ('deck', 'collection'))
- `uq_folder_share_token_hash` (share_token_hash)

#### `cards`
Individual card instances.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Card ID |
| name | VARCHAR(255) | NOT NULL, INDEXED | Card name |
| oracle_id | VARCHAR(128) | NOT NULL, INDEXED | Scryfall oracle ID |
| set_code | VARCHAR(10) | NOT NULL | Set code |
| collector_number | VARCHAR(20) | NOT NULL | Collector number |
| is_foil | BOOLEAN | DEFAULT FALSE | Foil flag |
| lang | VARCHAR(10) | DEFAULT 'en' | Language code |
| quantity | INTEGER | DEFAULT 1 | Quantity owned |
| date_bought | DATE | NULLABLE | Purchase date |
| folder_id | INTEGER | FK folder.id CASCADE, NOT NULL, INDEXED | Folder reference |
| created_at | DATETIME | NOT NULL | Creation timestamp |
| updated_at | DATETIME | NOT NULL | Last update |

**Constraints:**
- `ck_cards_quantity_nonneg` (quantity >= 0)

**Indexes:**
- `ix_cards_oracle_print` (oracle_id, set_code, collector_number, is_foil, lang)
- `ix_cards_folder_oracle` (folder_id, oracle_id)
- `ix_cards_folder_print` (folder_id, set_code, collector_number, lang, is_foil)

#### `folder_share`
Folder sharing permissions.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Share ID |
| folder_id | INTEGER | FK folder.id CASCADE, NOT NULL | Folder reference |
| shared_user_id | INTEGER | FK users.id CASCADE, NOT NULL | Shared with user |
| permission | VARCHAR(20) | DEFAULT 'view' | view/edit |
| created_at | DATETIME | NOT NULL | Share timestamp |

**Constraints:**
- `uq_folder_share_folder_user` (folder_id, shared_user_id)

#### `folder_roles`
Role-based folder categorization.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Role ID |
| folder_id | INTEGER | FK folder.id CASCADE, NOT NULL | Folder reference |
| role | VARCHAR(64) | NOT NULL | Role name |
| created_at | DATETIME | NOT NULL | Assignment timestamp |

**Constraints:**
- `uq_folder_roles_folder_role` (folder_id, role)

### Games Domain

#### `game_sessions`
Individual game records.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Session ID |
| played_at | DATETIME | NOT NULL, INDEXED | Game timestamp |
| winner_user_id | INTEGER | FK users.id, NULLABLE | Winner reference |
| notes | TEXT | NULLABLE | Game notes |
| created_at | DATETIME | NOT NULL | Record creation |
| updated_at | DATETIME | NOT NULL | Last update |

#### `game_pods`
Recurring play groups.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Pod ID |
| name | VARCHAR(120) | NOT NULL | Pod name |
| owner_user_id | INTEGER | FK users.id, NOT NULL | Pod owner |
| is_active | BOOLEAN | DEFAULT TRUE | Active flag |
| created_at | DATETIME | NOT NULL | Creation timestamp |
| updated_at | DATETIME | NOT NULL | Last update |

#### `game_pod_members`
Pod membership.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Membership ID |
| pod_id | INTEGER | FK game_pods.id CASCADE, NOT NULL | Pod reference |
| user_id | INTEGER | FK users.id CASCADE, NOT NULL | Member reference |
| joined_at | DATETIME | NOT NULL | Join timestamp |

**Constraints:**
- `uq_game_pod_members_pod_user` (pod_id, user_id)

#### `game_seats`
Player positions in a game.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Seat ID |
| session_id | INTEGER | FK game_sessions.id CASCADE, NOT NULL | Session reference |
| seat_number | INTEGER | NOT NULL | Seat position (1-N) |
| user_id | INTEGER | FK users.id, NULLABLE | Player reference |
| player_name | VARCHAR(120) | NULLABLE | Guest player name |
| folder_id | INTEGER | FK folder.id, NULLABLE | Deck used |
| placement | INTEGER | NULLABLE | Final placement |
| eliminated_at_turn | INTEGER | NULLABLE | Elimination turn |

**Constraints:**
- `uq_game_seats_session_seat` (session_id, seat_number)

### Metadata & Caching

#### `oracle_tags`
Scryfall oracle-level card tags.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Tag ID |
| oracle_id | VARCHAR(128) | NOT NULL, INDEXED | Oracle reference |
| tag | VARCHAR(120) | NOT NULL, INDEXED | Tag name |
| source | VARCHAR(64) | DEFAULT 'system' | Tag source |
| created_at | DATETIME | NOT NULL | Creation timestamp |

**Constraints:**
- `uq_oracle_tags_oracle_tag` (oracle_id, tag)

#### `deck_tags`
Deck archetype/theme vocabulary.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Tag ID |
| name | VARCHAR(128) | UNIQUE, NOT NULL | Tag name |
| slug | VARCHAR(160) | UNIQUE, NOT NULL | URL slug |
| source | VARCHAR(32) | NOT NULL, INDEXED | system/user/edhrec |
| edhrec_category | VARCHAR(120) | NULLABLE, INDEXED | EDHREC category |
| created_at | DATETIME | NOT NULL | Creation timestamp |
| updated_at | DATETIME | NOT NULL | Last update |

#### `deck_tag_map`
Deck-to-tag associations.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Mapping ID |
| folder_id | INTEGER | FK folder.id CASCADE, NOT NULL | Deck reference |
| deck_tag_id | INTEGER | FK deck_tags.id CASCADE, NOT NULL | Tag reference |
| confidence | FLOAT | NULLABLE | Confidence score |
| source | VARCHAR(32) | NOT NULL, INDEXED | Assignment source |
| locked | BOOLEAN | DEFAULT FALSE | User-locked flag |
| created_at | DATETIME | NOT NULL | Assignment timestamp |
| updated_at | DATETIME | NOT NULL | Last update |

**Constraints:**
- `uq_deck_tag_map_folder_tag` (folder_id, deck_tag_id)

#### `commander_bracket_cache`
Commander bracket scoring cache.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Cache ID |
| folder_id | INTEGER | FK folder.id CASCADE, UNIQUE, NOT NULL | Deck reference |
| bracket | INTEGER | NULLABLE | Bracket tier (1-4) |
| score | FLOAT | NULLABLE | Bracket score |
| computed_at | DATETIME | NOT NULL | Computation timestamp |

#### `deck_stats`
Deck statistics cache.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Stats ID |
| folder_id | INTEGER | FK folder.id CASCADE, UNIQUE, NOT NULL | Deck reference |
| total_cards | INTEGER | DEFAULT 0 | Total card count |
| unique_cards | INTEGER | DEFAULT 0 | Unique card count |
| avg_cmc | FLOAT | NULLABLE | Average CMC |
| computed_at | DATETIME | NOT NULL | Computation timestamp |

#### `edhrec_cache`
EDHREC API response cache.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Cache ID |
| cache_key | VARCHAR(255) | UNIQUE, NOT NULL | Cache key |
| response_json | TEXT | NOT NULL | JSON response |
| cached_at | DATETIME | NOT NULL | Cache timestamp |
| expires_at | DATETIME | NOT NULL | Expiry timestamp |

#### `wishlist`
Card wishlist tracking.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Wishlist ID |
| user_id | INTEGER | FK users.id CASCADE, NOT NULL | User reference |
| oracle_id | VARCHAR(128) | NOT NULL, INDEXED | Card oracle ID |
| name | VARCHAR(255) | NOT NULL | Card name |
| status | VARCHAR(20) | DEFAULT 'wanted' | wanted/acquired/removed |
| priority | INTEGER | DEFAULT 0 | Priority level |
| notes | TEXT | NULLABLE | User notes |
| order_ref | VARCHAR(120) | NULLABLE | Order reference |
| created_at | DATETIME | NOT NULL | Creation timestamp |
| updated_at | DATETIME | NOT NULL | Last update |

**Constraints:**
- `uq_wishlist_user_oracle` (user_id, oracle_id)

#### `friend_card_requests`
Card borrow/trade requests between friends.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Request ID |
| requester_user_id | INTEGER | FK users.id CASCADE, NOT NULL | Requester |
| owner_user_id | INTEGER | FK users.id CASCADE, NOT NULL | Card owner |
| card_id | INTEGER | FK cards.id CASCADE, NOT NULL | Card reference |
| status | VARCHAR(20) | DEFAULT 'pending' | pending/approved/declined |
| message | TEXT | NULLABLE | Request message |
| created_at | DATETIME | NOT NULL | Request timestamp |
| updated_at | DATETIME | NOT NULL | Status change |

**Constraints:**
- `ck_friend_card_requests_status` (status IN ('pending', 'approved', 'declined', 'returned'))

#### `audit_log`
System audit trail.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Log ID |
| user_id | INTEGER | FK users.id, NULLABLE | User reference |
| action | VARCHAR(120) | NOT NULL, INDEXED | Action type |
| resource_type | VARCHAR(64) | NULLABLE | Resource type |
| resource_id | INTEGER | NULLABLE | Resource ID |
| details | TEXT | NULLABLE | JSON details |
| ip_address | VARCHAR(45) | NULLABLE | Client IP |
| user_agent | TEXT | NULLABLE | User agent |
| created_at | DATETIME | NOT NULL, INDEXED | Action timestamp |

## Microservice Schemas

### `card_data` Schema
Oracle-level Scryfall data (managed by card-data microservice).

- `card_data.oracle_cards`: Collapsed oracle-level card data
- `card_data.oracle_faces`: Multi-faced card data
- `card_data.oracle_legalities`: Format legalities

### `price_service` Schema
MTGJSON pricing data (managed by price-service).

- `price_service.print_prices`: Print-level pricing cache

### `edhrec_service` Schema
EDHREC data cache (managed by edhrec-service).

- `edhrec_service.edhrec_cache`: EDHREC API response cache

### `user_manager` Schema
User service data (experimental microservice).

- `user_manager.users`: User accounts (future migration)

### `folder_service` Schema
Folder service data (experimental microservice).

- `folder_service.folders`: Folders (future migration)

## Relationships

### User Relationships
- User → Folders (1:N via `owner_user_id`)
- User → Cards (indirect via Folders)
- User → Games (N:M via `game_seats`)
- User → Pods (N:M via `game_pod_members`)
- User → Wishlist (1:N)
- User → Friends (N:M via `user_friends`)
- User → Followers (N:M via `user_follow`)

### Folder Relationships
- Folder → Cards (1:N, CASCADE DELETE)
- Folder → Shares (1:N, CASCADE DELETE)
- Folder → Roles (1:N, CASCADE DELETE)
- Folder → Stats (1:1, CASCADE DELETE)
- Folder → Bracket Cache (1:1, CASCADE DELETE)
- Folder → Deck Tags (N:M via `deck_tag_map`)

### Game Relationships
- Session → Seats (1:N, CASCADE DELETE)
- Session → Winner (N:1 to User)
- Seat → User (N:1)
- Seat → Folder (N:1)
- Pod → Members (1:N, CASCADE DELETE)

## Indexes Strategy

### Performance Indexes
- Foreign keys are indexed by default
- Composite indexes for common query patterns
- Timestamp columns for temporal queries
- Status/category columns for filtering

### Full-Text Search
- FTS tables created via `flask fts-ensure`
- Triggers maintain FTS indexes on card/folder changes
- Reindex via `flask fts-reindex`

## Migration Strategy

- Alembic manages schema versions
- Batch mode for SQLite compatibility
- Migrations in `backend/migrations/versions/`
- Run `flask db upgrade` to apply
- Rollback via `flask db downgrade`

## Data Integrity

### Constraints
- Foreign keys with CASCADE DELETE where appropriate
- Unique constraints on natural keys
- Check constraints for enums and ranges
- NOT NULL constraints on required fields

### Soft Deletes
- `archived_at` timestamp for users and folders
- Archived records excluded from queries via visibility filters
- Hard delete via admin tools only

## Performance Considerations

### Query Optimization
- Use `select_related`/`joinedload` for N+1 prevention
- Visibility filters applied at ORM level
- Pagination for large result sets
- Caching for expensive computations

### Maintenance
- Weekly `VACUUM ANALYZE` via pgmaintenance service
- Index rebuilds after bulk operations
- Stats refresh via `flask refresh-*` commands

## Security

### Access Control
- Per-request visibility filters enforce ownership
- Public folders accessible to all
- Shared folders via explicit grants
- Friend relationships enable cross-user visibility

### Data Protection
- Password hashing via werkzeug
- API tokens hashed with SHA256
- Session tokens in secure cookies
- CSRF protection on mutations
