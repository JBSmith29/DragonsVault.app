# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project adheres to Semantic Versioning.

## [1.0.0](https://github.com/JBSmith29/DragonsVault.app/compare/v0.1.0...v1.0.0) (2026-07-10)


### ⚠ BREAKING CHANGES

* game-engine UI/API endpoints and service container were removed.

### Features

* add documentation hosting via nginx and GitHub Pages ([f9f4dfa](https://github.com/JBSmith29/DragonsVault.app/commit/f9f4dfa95e15cb74b55d47710b5c5f155c7d37cd))
* card detail panel for opening hand simulator ([5acedff](https://github.com/JBSmith29/DragonsVault.app/commit/5acedffd57f833b781a9a40cc9c0172d1643f62c))
* **cards:** EDHREC-style as-you-type card-name autocomplete ([070a206](https://github.com/JBSmith29/DragonsVault.app/commit/070a20678af1e6d3c30c8ad37eb3dbd75c51a60b))
* **cards:** multi-keyword search — each evergreen keyword individually searchable ([f5b355c](https://github.com/JBSmith29/DragonsVault.app/commit/f5b355cd5073a0b645787682c3339df0cb8df7ed))
* collapsible Synergy Recommendations and improved Tokens panel on folder detail ([a58330c](https://github.com/JBSmith29/DragonsVault.app/commit/a58330cc09715982dc006762d8c5d5b7934ef976))
* comprehensive quality improvements and performance optimizations ([cd644fe](https://github.com/JBSmith29/DragonsVault.app/commit/cd644fe6e052c800860a358fccda78f76a4d9f23))
* comprehensive security and documentation improvements ([81dbb11](https://github.com/JBSmith29/DragonsVault.app/commit/81dbb1157ef6c825b90274fc9d127bf175efa873))
* **game-vault:** add decks manually (create + edit, optional decklist paste) ([95d969f](https://github.com/JBSmith29/DragonsVault.app/commit/95d969f4a9d32d3a025d29bc07dbf9a9c9bfe769))
* **game-vault:** copy decklist button in the deck detail view ([81843cf](https://github.com/JBSmith29/DragonsVault.app/commit/81843cf78fdf2a68e1ac70a6d07d8a545c3e8b22))
* **game-vault:** games search, deck detail, head-to-head, CSV export, refresh-all + small-phone UX ([8a45c33](https://github.com/JBSmith29/DragonsVault.app/commit/8a45c33dd83f5e8a17992749ee793cd9def0cccc))
* **game-vault:** metrics — infinite wins by player; drop timeline + streaks ([4895611](https://github.com/JBSmith29/DragonsVault.app/commit/4895611591f50cd6e3ca20cb61c3a1eb74d5c38d))
* **game-vault:** Metrics tab, deck brackets (estimate + manual), refresh ([dac3f0d](https://github.com/JBSmith29/DragonsVault.app/commit/dac3f0d7b4e95955af6ade7360165093d20cf36a))
* **game-vault:** richer Game-log filter for finding games to update ([a3a03d8](https://github.com/JBSmith29/DragonsVault.app/commit/a3a03d8fafc009b123a90ca3ed637cce1b9df5e7))
* **game-vault:** self-contained Magic game logger ([395f959](https://github.com/JBSmith29/DragonsVault.app/commit/395f95983a0257a9fd7ae798c7e01a012c9a52b5))
* **games:** Archidekt deck import — schema + materialise-to-Folder service ([98de62d](https://github.com/JBSmith29/DragonsVault.app/commit/98de62df7906505a79b56dbb6bdf4f78298b0256))
* **games:** Archidekt integration foundation — pull a user's Commander decks ([f97a1db](https://github.com/JBSmith29/DragonsVault.app/commit/f97a1db31e92c67d20fc5b6eaa69f6d7e01adc68))
* **games:** live "Pull from Archidekt" deck picker on the game log form ([ffecbed](https://github.com/JBSmith29/DragonsVault.app/commit/ffecbed7b48d8c06ce92f6ce9e5d96bd40e0fcc6))
* **games:** pod manager — set a player's Archidekt username ([5c1c159](https://github.com/JBSmith29/DragonsVault.app/commit/5c1c159d9eb73ac7ccd6d6b1b63f7447c8c5f17d))
* **games:** prefer an imported deck's Archidekt bracket in the game snapshot ([3fefbfd](https://github.com/JBSmith29/DragonsVault.app/commit/3fefbfda8ace488aea036f89ee70ab8d2588324e))
* MTG insights suite — legality, value, compare, win-rate, mana base, archetype, budget, playgroup, proxy PDF, rules lookups ([1d64c7d](https://github.com/JBSmith29/DragonsVault.app/commit/1d64c7dec6a740e8f86ea8ee675d6fd8154eb5e8))
* opening hand automation — auto-tap, ETB triggers, mana pool ([36da540](https://github.com/JBSmith29/DragonsVault.app/commit/36da540ce1c0521ce871f292064c03aba1fc25de))
* opening hand simulator QoL improvements ([b3c40b4](https://github.com/JBSmith29/DragonsVault.app/commit/b3c40b4bbc63319863fbe36f85d6309904ee5f37))
* **opening-hand:** always show +1/+1 & −1/−1 counters on creatures in play ([3892065](https://github.com/JBSmith29/DragonsVault.app/commit/38920656765e4ae76ef694c463efef18af2f5a17))
* **opening-hand:** drop full-turn Auto Play; keep assistive automation ([500270f](https://github.com/JBSmith29/DragonsVault.app/commit/500270f5e9bf5a13be67bf39b266b1f7efae27e8))
* **opening-hand:** life tracker, +1/+1 counters, auto-tap on cast ([405065a](https://github.com/JBSmith29/DragonsVault.app/commit/405065a63ba50a6e58ee947abc0c43884bd7764a))
* **opening-hand:** mana automation — auto-tap to cast, Auto Play, mana HUD ([1447507](https://github.com/JBSmith29/DragonsVault.app/commit/14475071e7cd9278d10ebd8bddc06809312c8b7e))
* **opening-hand:** modern life tracker, centered in the bottom action bar ([82f165e](https://github.com/JBSmith29/DragonsVault.app/commit/82f165e83b24377acf6153f981315b65f351a6a5))
* **opening-hand:** status HUD above command zone; redesigned life tracker ([315f30c](https://github.com/JBSmith29/DragonsVault.app/commit/315f30c59b3023bb0c3ad9be870e3b4107c7e984))
* refactor card/deck flows and add frontend deploy plumbing ([114db2a](https://github.com/JBSmith29/DragonsVault.app/commit/114db2aef22a32a75476a4847f030f9bc6655904))
* remove Collection Value dashboard; opening-hand cleanup ([99b157a](https://github.com/JBSmith29/DragonsVault.app/commit/99b157a5f7ebdd6a20e9f130b37041c0b1849137))
* self-service password reset via email ([b9da4c7](https://github.com/JBSmith29/DragonsVault.app/commit/b9da4c7f90c5fdb05e2dcb4bcc21d439dc0cd11b))
* stable scrollbar gutter; dual +1/+1 and -1/-1 counters ([9e3fc54](https://github.com/JBSmith29/DragonsVault.app/commit/9e3fc54bc5b205b86c82dfff137a688bbe881d7c))
* **tokens:** show exact power/toughness, color, and produced token everywhere ([4067652](https://github.com/JBSmith29/DragonsVault.app/commit/4067652e345219ba0731778da0dc7717e5baeef8))


### Bug Fixes

* add card name fallback for Commander Spellbook combo hover previews ([b01ed47](https://github.com/JBSmith29/DragonsVault.app/commit/b01ed47f9dbb5314a3cbcb25945936dcf3093ffe))
* **cards:** tighten core-role/evergreen/land-type search on both browsers ([65ae76e](https://github.com/JBSmith29/DragonsVault.app/commit/65ae76e81d97d9bd7f9c1fbd4d06c18081efeaa3))
* **decks:** resolve /decks 500 from mixed-type bracket sort; harden spellbook 429 retries ([0f7be21](https://github.com/JBSmith29/DragonsVault.app/commit/0f7be21ab614cae43c46b698129b937c6a38b8a5))
* django-api PYTHONPATH missing /app/backend for shared module ([9c1da4c](https://github.com/JBSmith29/DragonsVault.app/commit/9c1da4cb7e83e9499bb43949242e786ce90b329c))
* **game-vault:** show the commander in the decklist view ([fe68435](https://github.com/JBSmith29/DragonsVault.app/commit/fe68435369538ccc97464bedbd5def68d997ddb0))
* harden auth/service APIs and improve mobile UX ([948289a](https://github.com/JBSmith29/DragonsVault.app/commit/948289a7414cc43e89083ae163d2da2ee16488ca))
* improve folder_detail UI and synergy recommendations ([09ababc](https://github.com/JBSmith29/DragonsVault.app/commit/09ababc181eac3d78b7e5b6cc496e3988b29f832))
* Next Turn draws only once; rules popup is on-demand ([59ccbcd](https://github.com/JBSmith29/DragonsVault.app/commit/59ccbcdb34bca8cfe9cb31a50c44444355bdbfc2))
* opening hand UX polish — guards, color picker, discard handler, shortcuts ([71a73e9](https://github.com/JBSmith29/DragonsVault.app/commit/71a73e93cc95e9ea3840be4545874c018810ac54))
* **opening-hand:** actually shuffle the library after a tutor ([af4f864](https://github.com/JBSmith29/DragonsVault.app/commit/af4f8643f7e8c0c4e1e716f7b8026a460f69b0da))
* **opening-hand:** auto-tap click now actually plays the card (and never blocks it) ([09bcf93](https://github.com/JBSmith29/DragonsVault.app/commit/09bcf93011b93290911fe273baadf2b37e49652d))
* **opening-hand:** commander excluded from deck; Next Turn draws once ([266b417](https://github.com/JBSmith29/DragonsVault.app/commit/266b417a7f27b469dbb3f07b27d99e1e606cac00))
* **opening-hand:** life tracker badge styling and de-dup guard ([e8d7770](https://github.com/JBSmith29/DragonsVault.app/commit/e8d77703f4f6bc9044d9141dd1974bd8f7a50578))
* **opening-hand:** recreate life counter as big transparent "− 40 +" ([6e7810b](https://github.com/JBSmith29/DragonsVault.app/commit/6e7810bc05d7fc0a1d52930815d58586719209ac))
* **opening-hand:** robustly exclude the commander from the shuffled deck ([d54131f](https://github.com/JBSmith29/DragonsVault.app/commit/d54131f72b214c65e455210d17ae29dc47e366bc))
* **opening-hand:** serialize draws (no duplicate card); net mana for filter lands ([1e7c617](https://github.com/JBSmith29/DragonsVault.app/commit/1e7c6179e2b3b63f9e251f9d0e4e9d0917257c19))
* **opening-hand:** style +1/+1 badge as Bootstrap pill, drop trigger panel ([23b8299](https://github.com/JBSmith29/DragonsVault.app/commit/23b82996fea37c989c9867c6823e0870fdba5d7a))
* resolve all restarting/unhealthy containers; clean up dead deps ([41f26e2](https://github.com/JBSmith29/DragonsVault.app/commit/41f26e22bf2c19657cf457a8e7641ddebb8afef8))
* resolve scheduler crash and nginx duplicate log_format; add scheduler status to admin dashboard ([bdea3fc](https://github.com/JBSmith29/DragonsVault.app/commit/bdea3fcaecd92b87bb28e608f3b04a242ebbc18c))
* restore issued_token=None in manage_api_token; bump CI to Node 24 ([d0a064e](https://github.com/JBSmith29/DragonsVault.app/commit/d0a064ef95888bc1f5b26260ae410aa6b61e0eb4))
* shorten 0031 migration revision ID to fit alembic_version varchar(32) ([163c2a4](https://github.com/JBSmith29/DragonsVault.app/commit/163c2a40ab35c796cfd6c3faa038089ce70e0edc))
* **tokens:** resolve real named tokens with art on card detail; dedupe across printings ([00dcc8e](https://github.com/JBSmith29/DragonsVault.app/commit/00dcc8ec41421500746d7018c693714489dbafeb))
* **tokens:** tie token image to its own power/toughness and color ([c5e6d3e](https://github.com/JBSmith29/DragonsVault.app/commit/c5e6d3ef7a5e88cd3edd86210f59ef73a8255d11))


### Performance Improvements

* **catalog:** index Scryfall prints by id instead of linear scan ([e1e9af6](https://github.com/JBSmith29/DragonsVault.app/commit/e1e9af6d1711b9b552688e30256cb7b33c75db41))
* **games:** dedupe identical metrics queries within a request ([4e9a2b8](https://github.com/JBSmith29/DragonsVault.app/commit/4e9a2b822a633482a6027cecaa97619adb26ac7b))
* **pricing:** prefer embedded catalog prices over per-card price-service call ([03a2ab1](https://github.com/JBSmith29/DragonsVault.app/commit/03a2ab10b768380ef6191b86ac15ae6f00d547d9))


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
- `html { scrollbar-gutter: stable }` is now set globally so navigating between pages of different lengths no longer shifts content horizontally when the scrollbar appears or disappears. `body.modal-open` keeps `padding-right: 0` so Bootstrap modals don't double-compensate.
- **Opening Hand counters** now track both `+1/+1` (green) and `−1/−1` (red) counters per card, render simultaneously, and stay visible on any board zone whenever a counter has been placed. Context menu adds entries for both kinds.
- **Repository hygiene**: removed the entire `backend/routes/`, `backend/utils/`, and `backend/viewmodels/` legacy shim directories along with all of `backend/services/*.py` (kept `services/refresh_scheduler.py` as the docker-compose entry point). All re-exported their canonical modules under `core.*`/`shared.*` and had no remaining imports. Eight historical "completion summary" markdowns moved from the repo root into `docs/history/` with a small index. Removed the duplicate `Config = _select_config()` call and the dead `_validate_csrf_token` before-request hook in `app.py`.

### Fixed
- Migration `0014_remove_build_a_deck` imported `sa` from its docstring but never into scope, raising `NameError` on fresh installs. Fixed by importing `sqlalchemy as sa` explicitly.
- `GET /api/folders` used `selectinload(Folder.owner)` — but `Folder.owner` is a text column, not a relationship. Corrected to `Folder.owner_user` so friend-shared folder access works end-to-end.
- Duplicate test module name (`tests/routes/test_proxy_decks.py` and `tests/services/test_proxy_decks.py`) caused collection to fail when both were picked up. The services file is now `test_proxy_deck_parsing.py`.
- `test_build_folder_detail_commander_context_builds_media_bracket_links_and_edhrec` assertion updated to match actual filter-and-mark behavior (in-deck recommendations are removed, not flagged).
- Legal pages no longer show a stale "November 17, 2025" date; the label now derives from env vars or the process start date.
- **Opening Hand "Next Turn"** now awaits the draw fetch via `oh.drawCards(1)` instead of clicking the draw button. The previous implementation could leave the draw button disabled in flight, causing Next Turn to silently skip the draw on the next press.
- 

### Removed
- **Collection Value dashboard** removed at user request — the widget, route module (`/api/collection/value*`), `collection_value_service`, JS module, dashboard partial, and `CollectionValueSnapshot` model are gone. Migration `0032_drop_collection_value_snapshots` drops the persisted table.
- **Auto-tap mana on cast** and the **mana pool tracker** removed from the Opening Hand simulator. The Auto toggle now only governs ETB trigger automation (draw, tokens, scry, search prompts).
- 

[Unreleased]: https://github.com/JBSmith29/DragonsVault/compare/main...HEAD
