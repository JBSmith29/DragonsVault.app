# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project adheres to Semantic Versioning.

## [1.0.0](https://github.com/JBSmith29/DragonsVault.app/compare/v0.1.0...v1.0.0) (2026-04-27)


### ⚠ BREAKING CHANGES

* game-engine UI/API endpoints and service container were removed.

### Features

* card detail panel for opening hand simulator ([5acedff](https://github.com/JBSmith29/DragonsVault.app/commit/5acedffd57f833b781a9a40cc9c0172d1643f62c))
* collapsible Synergy Recommendations and improved Tokens panel on folder detail ([a58330c](https://github.com/JBSmith29/DragonsVault.app/commit/a58330cc09715982dc006762d8c5d5b7934ef976))
* refactor card/deck flows and add frontend deploy plumbing ([114db2a](https://github.com/JBSmith29/DragonsVault.app/commit/114db2aef22a32a75476a4847f030f9bc6655904))
* self-service password reset via email ([b9da4c7](https://github.com/JBSmith29/DragonsVault.app/commit/b9da4c7f90c5fdb05e2dcb4bcc21d439dc0cd11b))


### Bug Fixes

* django-api PYTHONPATH missing /app/backend for shared module ([9c1da4c](https://github.com/JBSmith29/DragonsVault.app/commit/9c1da4cb7e83e9499bb43949242e786ce90b329c))
* harden auth/service APIs and improve mobile UX ([948289a](https://github.com/JBSmith29/DragonsVault.app/commit/948289a7414cc43e89083ae163d2da2ee16488ca))
* resolve all restarting/unhealthy containers; clean up dead deps ([41f26e2](https://github.com/JBSmith29/DragonsVault.app/commit/41f26e22bf2c19657cf457a8e7641ddebb8afef8))
* resolve scheduler crash and nginx duplicate log_format; add scheduler status to admin dashboard ([bdea3fc](https://github.com/JBSmith29/DragonsVault.app/commit/bdea3fcaecd92b87bb28e608f3b04a242ebbc18c))
* restore issued_token=None in manage_api_token; bump CI to Node 24 ([d0a064e](https://github.com/JBSmith29/DragonsVault.app/commit/d0a064ef95888bc1f5b26260ae410aa6b61e0eb4))


### Code Refactoring

* retire game-engine service, migrate CI to Hatch, and add Sphinx docs ([bc3ab51](https://github.com/JBSmith29/DragonsVault.app/commit/bc3ab517ea57287ad8a0f8ee24f00178d8794f09))

## [Unreleased]

### Added
- 

### Changed
- 

### Fixed
- 

### Removed
- 

[Unreleased]: https://github.com/JBSmith29/DragonsVault/compare/main...HEAD
