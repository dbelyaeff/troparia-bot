# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.5] - 2026-04-22

### Added
- **Friendly Welcome Experience**: Implemented a descriptive welcome message with emojis for the `/start` command that explains the bot's features (Calendar, PDF generation, Troparia selection).
- **Catch-all Unknown Message Handler**: Both Telegram and MAX bots now respond to unrecognized text or media with the welcome message and a fresh calendar, improving discoverability and helping stuck users.

## [1.2.4] - 2026-04-19

### Fixed
- **PDF Layout Optimization**: Improved column/page breaking logic to prevent section headings from being separated from their content at the bottom of a page. Now, headings and the start of the next item are kept together.

## [1.2.3] - 2026-04-19

### Changed
- **Improved UI Labels**: Selection buttons now clearly indicate the text type (e.g., "Тропарь:", "Ин тропарь:", "Кондак:"), making it easier to distinguish multiple items from the same section.
- **Preserved Original Types**: The bot now preserves and displays the original liturgical types (including "Ин тропарь") in both the UI and the generated PDF.

## [1.2.2] - 2026-04-19

### Fixed
- **Liturgical Text Parsing**: Significantly improved "Bogosluzhebnye Ukazaniya" parsing to capture "праздника", "святого", and other common terms.
- **Fuzzy Section Mapping**: Implemented fuzzy matching for section names in the full instructions text, ensuring saints and feasts mentioned anywhere on the page are included.
- **Section Name Preservation**: The bot now preserves original section names (e.g., "Недели жен-мироносиц") instead of using generic labels (e.g., "Тропарь (Триоди)").
- **"Ин тропарь" Support**: Fixed case-sensitivity issue in item type normalization, ensuring "Another troparion" texts are correctly identified and included.

## [1.2.1] - 2026-04-18

### Changed
- **Adaptive PDF Font Size**: Optimized `FONT_MAX` (25pt) to fill the page elegantly for single items without word-wrap breakage.
- **Improved UX/Navigation**: Switched to text-sending buttons in both Telegram (`ReplyKeyboard`) and MAX (`MessageButton`) to provide immediate feedback by making user selections visible in chat.
- **Robust Date Parsing**: Implemented regex-based date parsing from text messages for seamless transition to text-based navigation.

## [1.2.0] - 2026-04-15

### Changed
- **Migrated from maxo to maxapi**: Replaced the previous `maxo` library with the official `maxapi` (`max-messenger-client/max-botapi-python`) for improved reliability and better alignment with MAX messenger standards.
- **Improved Dispatcher Logic**: Updated `max_bot.py` to use `maxapi.Dispatcher` with `magic_filter` (F) for robust and precise event routing.
- **Enhanced Keyboard Construction**: Switched to `InlineKeyboardBuilder` for cleaner and more maintainable keyboard layouts.
- **Updated Test Infrastructure**: Refactored the MAX bot test suite to be fully compatible with `maxapi` event models and method signatures.
- **File Upload Optimization**: Integrated `bot.upload_file_buffer` for more efficient PDF document delivery.

## [1.1.1] - 2026-04-11

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
