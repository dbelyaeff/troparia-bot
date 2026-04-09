# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-04-09

### Added
- Webhook support in `bot.py` via `python-telegram-bot[webhooks]`.
- Docker network configuration for integration with Caddy proxy.
- Port `8080` exposed for internal communication.
- Deployment automation via `Makefile`.
- Project documentation: `CHANGELOG.md` and `AGENTS.md`.

### Changed
- Switched from Long Polling to Webhooks for production deployment.
- Production environment now runs on `tgmx` server.
- Reverse proxy configured via Caddy.

## [1.0.0] - 2026-04-09
- Initial release of the Troparia Bot.
- Support for generating PDF with Troparia and Kontakia.
- Integration with azbyka.ru.
