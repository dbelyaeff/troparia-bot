# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **maxo library integration**: Migrated `max_bot.py` from manual `httpx` logic to `maxo` library for a more robust, event-driven architecture.
- **Improved InlineKeyboard logic**: Resolved persistent `mid=None` issues by using `maxo`'s built-in update parsing and `F` filters.
- **Automated file handling**: Integrated `maxo.Bot.upload_media` for reliable PDF delivery.

## [1.1.0] - 2026-04-09

### Added
- **MAX Messenger Support**: New bot service for MAX messenger platform.
- **Redis State Management**: Persistent user state using Redis storage.
- **Automated Testing Suite**: Full suite of unit and integration tests with ~85% coverage.
- **Multi-platform shared logic**: Extracted business logic to `shared_logic.py`.
- **MAX Bot API Client**: Custom client for MAX platform.

### Changed
- **Telegram Bot Refactoring**: Migrated `bot.py` to use `shared_logic` and Redis state.
- **Infrastructure**: Updated `docker-compose.yml` with `redis` and `max-bot` services.
- **Deployment**: Updated `Makefile` with testing and multi-service deployment support.

### Fixed
- User state persistence across container restarts.
- Consistent liturgical date calculation across platforms.
