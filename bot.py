#!/usr/bin/env python3
import io
import logging
import os
from datetime import datetime, timedelta
from urllib.parse import urlparse

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)

from generator import generate_pdf_bytes
from shared_logic import (
    get_liturgical_date, get_date_human, fetch_data_for_date,
    get_pdf_sections, DAYS_RU, MONTHS_RU
)
from state import state_manager

import logging.handlers

LOG_DIR = os.environ.get("LOG_DIR", "logs")
if not os.path.isabs(LOG_DIR):
    LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), LOG_DIR)
os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger("telegram_bot")
logger.setLevel(logging.INFO)

# File handler with rotation
file_handler = logging.handlers.TimedRotatingFileHandler(
    os.path.join(LOG_DIR, "telegram.log"), when="D", interval=1, backupCount=7
)
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(file_handler)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s"))
logger.addHandler(console_handler)

FONT_PATH = os.environ.get("FONT_PATH", "/app/fonts/PonomarUnicode.otf")

# ─── Клавиатуры ───

def build_date_keyboard(week_offset: int = 0) -> InlineKeyboardMarkup:
    liturgical_today = get_liturgical_date().date()
    start = liturgical_today + timedelta(weeks=week_offset)
    buttons = []
    
    if week_offset == 0:
        d = start
        weekday_full = "Воскресенье" if d.weekday() == 6 else DAYS_RU[d.weekday()]
        label = f"📅 Сегодня {d.day} {MONTHS_RU[d.month]} ({weekday_full})"
        buttons.append([InlineKeyboardButton(label, callback_data=f"date:{d.isoformat()}")])
        
        row = []
        for i in range(1, 7):
            d = start + timedelta(days=i)
            weekday = DAYS_RU[d.weekday()]
            if d.weekday() == 6:
                if row: buttons.append(row)
                row = []
                label = f"{d.day} {MONTHS_RU[d.month]} ({weekday}) ✝️"
                buttons.append([InlineKeyboardButton(label, callback_data=f"date:{d.isoformat()}")])
            else:
                label = f"{d.day} {MONTHS_RU[d.month]} ({weekday})"
                row.append(InlineKeyboardButton(label, callback_data=f"date:{d.isoformat()}"))
                if len(row) == 2:
                    buttons.append(row); row = []
        if row: buttons.append(row)
    else:
        row = []
        for i in range(7):
            d = start + timedelta(days=i)
            weekday = DAYS_RU[d.weekday()]
            if d.weekday() == 6:
                if row: buttons.append(row)
                row = []
                label = f"{d.day} {MONTHS_RU[d.month]} ({weekday}) ✝️"
                buttons.append([InlineKeyboardButton(label, callback_data=f"date:{d.isoformat()}")])
            else:
                label = f"{d.day} {MONTHS_RU[d.month]} ({weekday})"
                row.append(InlineKeyboardButton(label, callback_data=f"date:{d.isoformat()}"))
                if len(row) == 2:
                    buttons.append(row); row = []
        if row: buttons.append(row)

    nav = []
    if week_offset > 0:
        nav.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"week:{week_offset - 1}"))
    nav.append(InlineKeyboardButton("Вперёд ▶️", callback_data=f"week:{week_offset + 1}"))
    buttons.append(nav)
    return InlineKeyboardMarkup(buttons)

def build_selection_keyboard(pairs: list, selections: dict) -> InlineKeyboardMarkup:
    buttons = []
    for pair_idx, pair in enumerate(pairs):
        pair_id = f"pair_{pair_idx}"
        is_selected = selections.get(pair_id, True)
        check = "✅" if is_selected else "❌"
        label = f"{check} {pair.get('section', '')} (гл.{pair.get('tropar_glas', '')}/{pair.get('kontak_glas', '')})"
        buttons.append([InlineKeyboardButton(label, callback_data=f"toggle:{pair_id}")])
    
    if pairs:
        all_selected = all(selections.get(f"pair_{i}", True) for i in range(len(pairs)))
        toggle_all_text = "❌ Снять все" if all_selected else "✅ Выбрать все"
        buttons.append([InlineKeyboardButton(toggle_all_text, callback_data="toggle_all")])
    return InlineKeyboardMarkup(buttons)

def build_selection_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📄 Сгенерировать PDF"), KeyboardButton("⬅️ Назад")]],
        resize_keyboard=True
    )

# ─── Обработчики ───

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    await state_manager.clear_state(user_id)
    text = (
        "☦️ <b>Тропари и Кондаки на Часах</b>\n\n"
        "Бот генерирует PDF-документ с тропарями и кондаками\n"
        "для Часов (1-й, 3-й, 6-й, 9-й) согласно\n"
        "<b>Богослужебным указаниям</b>.\n\n"
        "📅 <b>Выберите дату:</b>"
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=build_date_keyboard(0))

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(update.effective_user.id)
    data = query.data
    user_state = await state_manager.get_state(user_id)

    if data.startswith("week:"):
        offset = int(data.split(":")[1])
        await query.edit_message_reply_markup(reply_markup=build_date_keyboard(offset))
        return

    if data.startswith("date:"):
        date_str = data.split(":")[1]
        date_human = get_date_human(date_str)
        await query.edit_message_text(f"⏳ Загружаю данные на <b>{date_human}</b>...", parse_mode="HTML")
        
        try:
            ukazaniya_text, pairs = await fetch_data_for_date(date_str)
            user_state.update({
                "selected_date": date_str,
                "pairs": pairs,
                "selections": {f"pair_{i}": True for i in range(len(pairs))}
            })
            await state_manager.set_state(user_id, user_state)
            
            await query.edit_message_text(
                f"📖 <b>Богослужебные указания</b> на <b>{date_human}</b>\n\n"
                f"<i>Что читается на Часах:</i>\n\n{ukazaniya_text}",
                parse_mode="HTML"
            )
            await query.message.reply_text(
                "🔹 <b>Выберите тропари и кондаки</b>",
                parse_mode="HTML",
                reply_markup=build_selection_keyboard(pairs, user_state["selections"])
            )
            await query.message.reply_text("Выбирайте тропари и кондаки 👆", reply_markup=build_selection_reply_keyboard())
        except Exception as e:
            logger.exception("Ошибка загрузки данных")
            await query.message.reply_text(f"❌ Ошибка: {e}", reply_markup=build_date_keyboard(0))
        return

    if data.startswith("toggle:"):
        pair_id = data.split(":")[1]
        selections = user_state.get("selections", {})
        selections[pair_id] = not selections.get(pair_id, True)
        user_state["selections"] = selections
        await state_manager.set_state(user_id, user_state)
        await query.edit_message_reply_markup(reply_markup=build_selection_keyboard(user_state["pairs"], selections))
        return

    if data == "toggle_all":
        pairs = user_state.get("pairs", [])
        selections = user_state.get("selections", {})
        all_selected = all(selections.get(f"pair_{i}", True) for i in range(len(pairs)))
        for i in range(len(pairs)):
            selections[f"pair_{i}"] = not all_selected
        user_state["selections"] = selections
        await state_manager.set_state(user_id, user_state)
        await query.edit_message_reply_markup(reply_markup=build_selection_keyboard(pairs, selections))
        return

async def common_generate_pdf(update: Update, user_id: str, user_state: dict):
    date_str = user_state.get("selected_date")
    pairs = user_state.get("pairs", [])
    selections = user_state.get("selections", {})
    
    if not date_str or not pairs:
        await update.effective_message.reply_text("❌ Сначала выберите дату.", reply_markup=build_date_keyboard(0))
        return

    selected_pairs = [pairs[i] for i in range(len(pairs)) if selections.get(f"pair_{i}", True)]
    if not selected_pairs:
        await update.effective_message.reply_text("❌ Выберите хотя бы одну пару!")
        return

    date_human = get_date_human(date_str)
    await update.effective_message.reply_text(f"⏳ Генерирую PDF на <b>{date_human}</b>...", parse_mode="HTML")
    
    try:
        pdf_bytes = generate_pdf_bytes(date_str, FONT_PATH, sections=get_pdf_sections(selected_pairs))
        await update.effective_message.reply_document(
            document=io.BytesIO(pdf_bytes),
            filename=f"Тропари_и_кондаки_{date_str}.pdf",
            caption=f"☦️ Тропари и кондаки на Часах ({date_human})"
        )
        await update.effective_message.reply_text("📅 <b>Выберите дату:</b>", parse_mode="HTML", reply_markup=build_date_keyboard(0))
        await update.effective_message.reply_text(".", reply_markup=ReplyKeyboardRemove())
    except Exception as e:
        logger.exception("Ошибка генерации PDF")
        await update.effective_message.reply_text(f"❌ Ошибка: {e}", reply_markup=build_date_keyboard(0))

async def reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = str(update.effective_user.id)
    user_state = await state_manager.get_state(user_id)

    if text == "📄 Сгенерировать PDF":
        await common_generate_pdf(update, user_id, user_state)
    elif text == "⬅️ Назад":
        await state_manager.clear_state(user_id)
        await update.message.reply_text("📅 <b>Выберите дату:</b>", parse_mode="HTML", reply_markup=build_date_keyboard(0))
        await update.message.reply_text(".", reply_markup=ReplyKeyboardRemove())

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN error")
        return
    
    if not os.path.exists(FONT_PATH):
        logger.error(f"FONT_PATH does not exist: {FONT_PATH}")
        # We don't exit here, might be a mock or temporary issue, 
        # but the generator will fail later. 
        # Actually, let's just log and continue for tests.

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply_handler))

    webhook_url = os.environ.get("TELEGRAM_WEBHOOK_URL") or os.environ.get("WEBHOOK_URL")
    webhook_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
    
    if webhook_url:
        webhook_path = urlparse(webhook_url).path.lstrip('/')
        port = int(os.environ.get("PORT", 8080))
        
        logger.info(f"Starting in WEBHOOK mode. URL: {webhook_url}")
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=webhook_path,
            webhook_url=webhook_url,
            secret_token=webhook_secret,
            drop_pending_updates=True
        )
    else:
        logger.info("Starting in POLLING mode")
        app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
