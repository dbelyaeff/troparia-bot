import io
import logging
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from contextlib import asynccontextmanager

from maxo.bot import Bot
from maxo.types import Keyboard, CallbackButton, LinkButton, FileAttachment as File, Message, Callback
from fastapi import FastAPI, Request, Header, HTTPException
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

logger = logging.getLogger("max_bot")
logger.setLevel(logging.INFO)

# File handler with rotation
file_handler = logging.handlers.TimedRotatingFileHandler(
    os.path.join(LOG_DIR, "max.log"), when="D", interval=1, backupCount=7
)
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(file_handler)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s"))
logger.addHandler(console_handler)

MAX_TOKEN = os.environ.get("MAX_BOT_TOKEN")
MAX_SECRET = os.environ.get("MAX_WEBHOOK_SECRET")
FONT_PATH = os.environ.get("FONT_PATH", "/app/fonts/PonomarUnicode.otf")

bot = maxo.Bot(token=MAX_TOKEN)
dp = maxo.Dispatcher(bot)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: register webhook
    webhook_url = os.environ.get("MAX_WEBHOOK_URL")
    if webhook_url:
        try:
            logger.info(f"Registering MAX webhook: {webhook_url}")
            await bot.set_webhook(url=webhook_url, secret=MAX_SECRET)
            logger.info("MAX webhook registered successfully")
        except Exception as e:
            logger.error(f"Failed to register MAX webhook: {e}")
    yield

app = FastAPI(lifespan=lifespan)

# Manual client removed in favor of maxo.Bot

# ─── Клавиатуры ───

def build_max_date_keyboard(week_offset: int = 0) -> InlineKeyboard:
    liturgical_today = get_liturgical_date().date()
    start = liturgical_today + timedelta(weeks=week_offset)
    buttons = []
    
    # Сегодня
    if week_offset == 0:
        d = start
        weekday = DAYS_RU[d.weekday()]
        buttons.append([InlineButton(text=f"📅 Сегодня {d.day} {MONTHS_RU[d.month]} ({weekday})", payload=f"date:{d.isoformat()}")])
        # Остальные дни
        row = []
        for i in range(1, 7):
            d = start + timedelta(days=i)
            row.append(InlineButton(text=f"{d.day} {MONTHS_RU[d.month]} ({DAYS_RU[d.weekday()]})", payload=f"date:{d.isoformat()}"))
            if len(row) == 2:
                buttons.append(row); row = []
        if row: buttons.append(row)
    else:
    start_of_week = liturgical_today + timedelta(weeks=week_offset)
    rows: List[List[CallbackButton]] = []
    
    # Navigation row
    nav_row = [
        CallbackButton(text="◀️", payload=f"date_week_{week_offset - 1}"),
        CallbackButton(text="▶️", payload=f"date_week_{week_offset + 1}")
    ]
    rows.append(nav_row)
    
    # Date buttons
    for i in range(7):
        date = start_of_week + timedelta(days=i)
        date_str = date.strftime("%Y-%m-%d")
        button_text = f"{date.day} {MONTHS_RU[date.month]} ({DAYS_RU[date.weekday()]})"
        rows.append([CallbackButton(text=button_text, payload=f"date_select_{date_str}")])
        
    return Keyboard(buttons=rows)

def build_max_selection_keyboard(pairs: list, selections: dict, lang: str = "ru", date_str: str = "") -> Keyboard:
    rows = [
        [CallbackButton(text="📜 Текст", payload=f"toggle:text:{lang}:{date_str}"),
         CallbackButton(text="🎵 Аудио", payload=f"toggle:audio:{lang}:{date_str}")],
        [CallbackButton(text="📅 Дата", payload="select_date"),
         CallbackButton(text="🌐 RU/EN", payload=f"toggle:lang:{lang}:{date_str}")]
    ]
    return Keyboard(buttons=rows)

# ─── Обработчики ───

@dp.message(lambda m: m.text == "/start")
async def handle_start(message: Message):
    user_id = message.sender.user_id
    await state_manager.clear_state(str(user_id))
    await bot.send_message(
         user_id=user_id,
         text="☦️ <b>Тропари и Кондаки</b>\n\nВыберите дату:",
         keyboard=build_max_date_keyboard(0)
    )

@dp.callback(lambda c: c.payload.startswith("date_week_"))
async def handle_date_nav(c: Callback):
    week_offset = int(c.payload.split("_")[2])
    kb = build_max_date_keyboard(week_offset)
    await bot.edit_message(message_id=c.message.mid, text="Выберите дату:", keyboard=kb)

@dp.callback(lambda c: c.payload.startswith("date_select_"))
async def handle_date_select(c: Callback):
    date_str = c.payload.split("_")[2]
    kb = build_max_selection_keyboard([], {}, "ru", date_str)
    await bot.edit_message(message_id=c.message.mid, text=f"Настройки для {date_str}:", keyboard=kb)

@dp.callback(lambda c: c.payload == "select_date")
async def handle_select_date_btn(c: Callback):
    kb = build_max_date_keyboard(0)
    await bot.edit_message(message_id=c.message.mid, text="Выберите дату:", keyboard=kb)

@dp.callback(lambda c: c.payload.startswith("toggle:"))
async def handle_toggle(c: Callback):
    parts = c.payload.split(":")
    user_id = c.user.user_id
    date_str = parts[3]
    date_human = get_date_human(date_str)
        await bot.edit_message(message_id=mid, text=f"⏳ Загружаю данные на {date_human}...")
    
    user_state = await state_manager.get_state(str(user_id))
    try:
        ukazaniya_text, pairs = await fetch_data_for_date(date_str)
        user_state.update({
            "selected_date": date_str,
            "pairs": pairs,
            "selections": {f"pair_{i}": True for i in range(len(pairs))}
        })
        await state_manager.set_state(str(user_id), user_state)
        
        text = f"📖 <b>Указания</b>: {ukazaniya_text}\n\n🔹 <b>Выберите тропари:</b>"
        if mid:
            await bot.edit_message(
                message_id=mid, 
                text=text, 
                keyboard=build_max_selection_keyboard(pairs, user_state["selections"])
            )
    except Exception as e:
        logger.exception("MAX data fetch error")
        if mid:
            await bot.edit_message(message_id=mid, text=f"❌ Ошибка: {e}")
            
    await bot.answer_callback(callback_id=callback.callback_id, notification="Дата выбрана")

@dp.callback(F.payload.startswith("toggle:"))
async def handle_toggle(callback: maxo.types.Callback):
    user_id = callback.user.user_id
    pair_id = callback.payload.split(":")[1]
    mid = callback.message.mid if callback.message else None
    
    user_state = await state_manager.get_state(str(user_id))
    selections = user_state.get("selections", {})
    selections[pair_id] = not selections.get(pair_id, True)
    user_state["selections"] = selections
    await state_manager.set_state(str(user_id), user_state)
    
    if mid:
        await bot.edit_message(
            message_id=mid,
            text="Обновлено (выбор тропарей):",
            keyboard=build_max_selection_keyboard(user_state["pairs"], selections)
        )
    await bot.answer_callback(callback_id=callback.callback_id, notification="Переключено")

@dp.callback(F.payload == "toggle_all")
async def handle_toggle_all(callback: maxo.types.Callback):
    user_id = callback.user.user_id
    mid = callback.message.mid if callback.message else None
    
    user_state = await state_manager.get_state(str(user_id))
    pairs = user_state.get("pairs", [])
    selections = user_state.get("selections", {})
    all_selected = all(selections.get(f"pair_{i}", True) for i in range(len(pairs)))
    
    for i in range(len(pairs)):
        selections[f"pair_{i}"] = not all_selected
    
    user_state["selections"] = selections
    await state_manager.set_state(str(user_id), user_state)
    
    if mid:
        await bot.edit_message(
            message_id=mid,
            text="Обновлено (все):",
            keyboard=build_max_selection_keyboard(pairs, selections)
        )
    await bot.answer_callback(callback_id=callback.callback_id, notification="Все переключено")

@dp.callback(F.payload == "generate_pdf")
async def handle_generate_pdf(callback: maxo.types.Callback):
    user_id = callback.user.user_id
    mid = callback.message.mid if callback.message else None
    user_state = await state_manager.get_state(str(user_id))
    
    date_str = user_state.get("selected_date")
    pairs = user_state.get("pairs")
    selections = user_state.get("selections")
    
    if not date_str or not pairs or not selections:
        if mid:
            await bot.edit_message(
                message_id=mid,
                text="⚠️ Сессия истекла или данные не выбраны. Начните сначала:",
                keyboard=build_max_date_keyboard(0)
            )
        await bot.answer_callback(callback_id=callback.callback_id, notification="Ошибка сессии")
        return

    selected_pairs = [pairs[i] for i in range(len(pairs)) if selections.get(f"pair_{i}", True)]
    
    if not selected_pairs:
        if mid:
            await bot.edit_message(
                message_id=mid,
                text="❌ Выберите хотя бы одну пару!",
                keyboard=build_max_selection_keyboard(pairs, selections)
            )
        return
    
    if mid:
        await bot.edit_message(message_id=mid, text="⏳ Генерирую PDF...")
    
    try:
        from unihttp.http import UploadFile
        pdf_bytes = generate_pdf_bytes(date_str, FONT_PATH, sections=get_pdf_sections(selected_pairs))
        upload_info = await bot.get_upload_url(type="file")
        media = await bot.upload_media(
            upload_url=upload_info.url,
            file=UploadFile(io.BytesIO(pdf_bytes), filename=f"Troparia_{date_str}.pdf")
        )
        await bot.send_message(
            user_id=user_id,
            text=f"☦️ PDF на {get_date_human(date_str)}",
            attachments=[File(token=media.token)]
        )
        if mid:
            await bot.edit_message(
                message_id=mid,
                text="✅ PDF отправлен! Выберите следующую дату:",
                keyboard=build_max_date_keyboard(0)
            )
    except Exception as e:
        logger.exception("MAX PDF error")
        if mid:
            await bot.edit_message(
                message_id=mid,
                text=f"❌ Ошибка генерации: {e}",
                keyboard=build_max_selection_keyboard(pairs, selections)
            )
    await bot.answer_callback(callback_id=callback.callback_id, notification="PDF сгенерирован")

@dp.callback(F.payload == "back_to_calendar")
async def handle_back_to_calendar(callback: maxo.types.Callback):
    user_id = callback.user.user_id
    mid = callback.message.mid if callback.message else None
    await state_manager.clear_state(str(user_id))
    if mid:
        await bot.edit_message(
            message_id=mid, 
            text="📅 Выберите дату:", 
            keyboard=build_max_date_keyboard(0)
        )
    await bot.answer_callback(callback_id=callback.callback_id, notification="Назад")

@app.post("/webhook/1f7c5225-1f1d-4c0c-b0b8-65a71b304b93")
async def max_webhook(request: Request, x_max_bot_api_secret: str = Header(None)):
    if x_max_bot_api_secret != MAX_SECRET:
        logger.warning(f"Invalid secret from {request.client.host}")
        raise HTTPException(status_code=403, detail="Invalid secret")
    
    update_data = await request.json()
    await dp.feed_update(update_data)
    return {"status": "ok"}
