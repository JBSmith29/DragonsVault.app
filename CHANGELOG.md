# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project adheres to Semantic Versioning.

## [1.0.0](https://github.com/JBSmith29/DragonsVault.app/compare/v0.1.0...v1.0.0) (2026-05-14)


### ⚠ BREAKING CHANGES

* game-engine UI/API endpoints and service container were removed.

### Features

* add documentation hosting via nginx and GitHub Pages ([f9f4dfa](https://github.com/JBSmith29/DragonsVault.app/commit/f9f4dfa95e15cb74b55d47710b5c5f155c7d37cd))
* card detail panel for opening hand simulator ([5acedff](https://github.com/JBSmith29/DragonsVault.app/commit/5acedffd57f833b781a9a40cc9c0172d1643f62c))
* collapsible Synergy Recommendations and improved Tokens panel on folder detail ([a58330c](https://github.com/JBSmith29/DragonsVault.app/commit/a58330cc09715982dc006762d8c5d5b7934ef976))
* comprehensive quality improvements and performance optimizations ([cd644fe](https://github.com/JBSmith29/DragonsVault.app/commit/cd644fe6e052c800860a358fccda78f76a4d9f23))
* comprehensive security and documentation improvements ([81dbb11](https://github.com/JBSmith29/DragonsVault.app/commit/81dbb1157ef6c825b90274fc9d127bf175efa873))
* MTG insights suite — legality, value, compare, win-rate, mana base, archetype, budget, playgroup, proxy PDF, rules lookups ([1d64c7d](https://github.com/JBSmith29/DragonsVault.app/commit/1d64c7dec6a740e8f86ea8ee675d6fd8154eb5e8))
* opening hand automation — auto-tap, ETB triggers, mana pool ([36da540](https://github.com/JBSmith29/DragonsVault.app/commit/36da540ce1c0521ce871f292064c03aba1fc25de))
* opening hand simulator QoL improvements ([b3c40b4](https://github.com/JBSmith29/DragonsVault.app/commit/b3c40b4bbc63319863fbe36f85d6309904ee5f37))
* **opening-hand:** life tracker, +1/+1 counters, auto-tap on cast ([405065a](https://github.com/JBSmith29/DragonsVault.app/commit/405065a63ba50a6e58ee947abc0c43884bd7764a))
* refactor card/deck flows and add frontend deploy plumbing ([114db2a](https://github.com/JBSmith29/DragonsVault.app/commit/114db2aef22a32a75476a4847f030f9bc6655904))
* self-service password reset via email ([b9da4c7](https://github.com/JBSmith29/DragonsVault.app/commit/b9da4c7f90c5fdb05e2dcb4bcc21d439dc0cd11b))


### Bug Fixes

* add card name fallback for Commander Spellbook combo hover previews ([b01ed47](https://github.com/JBSmith29/DragonsVault.app/commit/b01ed47f9dbb5314a3cbcb25945936dcf3093ffe))
* django-api PYTHONPATH missing /app/backend for shared module ([9c1da4c](https://github.com/JBSmith29/DragonsVault.app/commit/9c1da4cb7e83e9499bb43949242e786ce90b329c))
* harden auth/service APIs and improve mobile UX ([948289a](https://github.com/JBSmith29/DragonsVault.app/commit/948289a7414cc43e89083ae163d2da2ee16488ca))
* improve folder_detail UI and synergy recommendations ([09ababc](https://github.com/JBSmith29/DragonsVault.app/commit/09ababc181eac3d78b7e5b6cc496e3988b29f832))
* Next Turn draws only once; rules popup is on-demand ([59ccbcd](https://github.com/JBSmith29/DragonsVault.app/commit/59ccbcdb34bca8cfe9cb31a50c44444355bdbfc2))
* opening hand UX polish — guards, color picker, discard handler, shortcuts ([71a73e9](https://github.com/JBSmith29/DragonsVault.app/commit/71a73e93cc95e9ea3840be4545874c018810ac54))
* **opening-hand:** life tracker badge styling and de-dup guard ([e8d7770](https://github.com/JBSmith29/DragonsVault.app/commit/e8d77703f4f6bc9044d9141dd1974bd8f7a50578))
* **opening-hand:** style +1/+1 badge as Bootstrap pill, drop trigger panel ([23b8299](https://github.com/JBSmith29/DragonsVault.app/commit/23b82996fea37c989c9867c6823e0870fdba5d7a))
* resolve all restarting/unhealthy containers; clean up dead deps ([41f26e2](https://github.com/JBSmith29/DragonsVault.app/commit/41f26e22bf2c19657cf457a8e7641ddebb8afef8))
* resolve scheduler crash and nginx duplicate log_format; add scheduler status to admin dashboard ([bdea3fc](https://github.com/JBSmith29/DragonsVault.app/commit/bdea3fcaecd92b87bb28e608f3b04a242ebbc18c))
* restore issued_token=None in manage_api_token; bump CI to Node 24 ([d0a064e](https://github.com/JBSmith29/DragonsVault.app/commit/d0a064ef95888bc1f5b26260ae410aa6b61e0eb4))
* shorten 0031 migration revision ID to fit alembic_version varchar(32) ([163c2a4](https://github.com/JBSmith29/DragonsVault.app/commit/163c2a40ab35c796cfd6c3faa038089ce70e0edc))


### Code Refactoring

* retire game-engine service, migrate CI to Hatch, and add Sphinx docs ([bc3ab51](https://github.com/JBSmith29/DragonsVault.app/commit/bc3ab517ea57287ad8a0f8ee24f00178d8794f09))

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
- **Opening Hand simulator** — full automation pass over playtesting:
  - Quality-of-life: Next Turn button (untap + draw), single-click land plays, undo stack, keyboard shortcuts (D/U/N/M/Z/?/Esc), turn counter, hand-size badge, lands-played badge, localStorage persistence with 6-hour TTL, reset confirmation.
  - Automation engine: auto-tap mana sources on click with mana pool display, "any color" picker, ETB trigger detection (draw / discard / tokens / scry / search / +1/+1 counter / triggered abilities), pending-trigger panel with skip/resolve, mana pool clears on Next Turn.
  - Life tracker with +/- buttons, click-to-edit life total, low-life and dead-life styling, opponent pod manager with per-opponent life and commander damage tracking, lethal commander damage indicator at 21+.
  - +1/+1 counter tracker with badge overlay on creatures, click to add, right-click to remove, context-menu integration, persistence per deck.
  - Auto-tap mana on cast: when casting a non-land from hand to the battlefield, the simulator auto-taps untapped lands matching the mana cost, deducts from the existing mana pool first, and warns if mana is short.

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
