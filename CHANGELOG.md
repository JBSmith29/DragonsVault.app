# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project adheres to Semantic Versioning.

## [Unreleased]

### Added
- **Deck Legality Checker** — validates decks against Commander, Standard, Pioneer, Modern, Legacy, Vintage, Pauper, and Brawl with copy-limit, singleton, and color-identity checks.
- **Collection Value dashboard** — live totals, 30-day trend sparkline, top cards, and persisted snapshots via the new `collection_value_snapshots` table.
- **Deck Comparison tool** — side-by-side diff of shared/unique cards, mana curve, color pips, and type counts.
- **Deck Win Rate Analytics** — per-deck overall record, seat breakdown, matchup table, and recent-window comparison.
- **Mana Base Analysis** — land classification (basic/fetch/shock/check/pain/dual/etc.), color-source totals, and warning heuristics.
- **Proxy Printing** — downloadable PDF with a 3x3 grid of playtest proxies per letter page, rendered with zero external dependencies.
- **Rules Engine integration** — inline keyword-ability lookups on card detail, linking Magic keywords to their comprehensive-rules entries via a new `POST /api/rules/keywords` endpoint.
- **Deck Archetype Classification** — explainable scoring across aggro/control/midrange/combo/stax/ramp/tribal/tokens with per-archetype reasons.
- **Budget Deck Suggestions** — cheaper alternatives for expensive cards, preferring items already in the user's collection.
- **Playgroup Stats** — per-pod player standings, longest streak, commander frequency, and meta diversity (Shannon entropy).
- **Card condition tracking** — NM/LP/MP/HP/DMG grade on each owned card. Editable from card detail, importable from the `condition` CSV column (accepts aliases like "Near Mint", "lightly played").
- Deck Insights panel on folder detail pages wiring the legality/archetype/mana-base/budget/win-rate tabs, Compare modal, and Proxy PDF download.
- Collection Value dashboard widget on the main user dashboard.
- Playgroup Stats button on the pod management page.
- `/api/openapi.json` now exposes schemas for every new endpoint; the stale `Game` stub was removed.
- `LEGAL_LAST_UPDATED` and `LEGAL_LAST_UPDATED_DATE` env vars to control the legal-page "last updated" label instead of a hardcoded date.

### Changed
- `config._select_config` now warns in dev/testing when `SECRET_KEY` is weak or unset (production already refused to boot).
- Migration `0015_add_build_sessions_v2` no longer double-creates column-level indexes on first install.
- Migration `0025_increase_commander_oracle_id_length` now uses `batch_alter_table` so it applies cleanly on SQLite.

### Fixed
- Migration `0014_remove_build_a_deck` imported `sa` from its docstring but never into scope, raising `NameError` on fresh installs. Fixed by importing `sqlalchemy as sa` explicitly.
- `GET /api/folders` used `selectinload(Folder.owner)` — but `Folder.owner` is a text column, not a relationship. Corrected to `Folder.owner_user` so friend-shared folder access works end-to-end.
- Duplicate test module name (`tests/routes/test_proxy_decks.py` and `tests/services/test_proxy_decks.py`) caused collection to fail when both were picked up. The services file is now `test_proxy_deck_parsing.py`.
- `test_build_folder_detail_commander_context_builds_media_bracket_links_and_edhrec` assertion updated to match actual filter-and-mark behavior (in-deck recommendations are removed, not flagged).
- Legal pages no longer show a stale "November 17, 2025" date; the label now derives from env vars or the process start date.
- 

### Removed
- 

[Unreleased]: https://github.com/JBSmith29/DragonsVault/compare/main...HEAD
