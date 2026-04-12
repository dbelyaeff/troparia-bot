import io
import logging
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from contextlib import asynccontextmanager

import maxo
from maxo.bot import Bot
from maxo.types import Keyboard, CallbackButton, LinkButton, FileAttachment as File, Message, User
from maxo.routing.signals.update import MaxoUpdate
from maxo.routing.updates.updates import Updates
from maxo.routing.updates.message_callback import CallbackQuery
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

bot = Bot(token=MAX_TOKEN)
dp = maxo.Dispatcher()

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

# ─── Клавиатуры ───

def build_max_date_keyboard(week_offset: int = 0) -> Keyboard:
    liturgical_today = get_liturgical_date().date()
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
        weekday = DAYS_RU[date.weekday()]
        label = f"{date.day} {MONTHS_RU[date.month]} ({weekday})"
        if date == liturgical_today:
            label = f"📅 {label}"
        rows.append([CallbackButton(text=label, payload=f"date_select_{date_str}")])
        
    return Keyboard(buttons=rows)

def build_max_selection_keyboard(pairs: list, selections: dict, lang: str = "ru", date_str: str = "") -> Keyboard:
    rows = []
    
    # Display toggle for each pair
    for i, pair in enumerate(pairs):
        pair_id = f"pair_{i}"
        is_selected = selections.get(pair_id, True)
        icon = "✅" if is_selected else "⬜"
        rows.append([CallbackButton(
            text=f"{icon} {pair.get('section', '')}", 
            payload=f"toggle:{pair_id}:{lang}:{date_str}"
        )])

    # Language and Actions row
    action_rows = [
        [CallbackButton(text=f"🌐 Язык: {lang.upper()}", payload=f"toggle:lang:{lang}:{date_str}")],
        [CallbackButton(text="📄 Сгенерировать PDF", payload=f"generate:{lang}:{date_str}")],
        [CallbackButton(text="⬅️ К календарю", payload="select_date")]
    ]
    rows.extend(action_rows)
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

@dp.callback_query(lambda c: c.payload.startswith("date_week_"))
async def handle_date_nav(c: CallbackQuery):
    week_offset = int(c.payload.split("_")[2])
    kb = build_max_date_keyboard(week_offset)
    if c.message:
        await bot.edit_message(message_id=c.message.mid, text="Выберите дату:", keyboard=kb)
    await bot.answer_callback(callback_id=c.callback_id)

@dp.callback_query(lambda c: c.payload.startswith("date_select_"))
async def handle_date_select(c: CallbackQuery):
    date_str = c.payload.split("_")[2]
    user_id = c.user.user_id
    mid = c.message.mid if c.message else None
    
    if mid:
        await bot.edit_message(message_id=mid, text="⏳ Загружаю данные...")
    
    try:
        ukazaniya_text, pairs = await fetch_data_for_date(date_str)
        user_state = {
            "selected_date": date_str,
            "pairs": pairs,
            "selections": {f"pair_{i}": True for i in range(len(pairs))},
            "lang": "ru"
        }
        await state_manager.set_state(str(user_id), user_state)
        
        text = f"📖 <b>Указания</b>: {ukazaniya_text}\n\n🔹 <b>Настройте PDF:</b>"
        if mid:
            await bot.edit_message(
                message_id=mid,
                text=text,
                keyboard=build_max_selection_keyboard(pairs, user_state["selections"], "ru", date_str)
            )
    except Exception as e:
        logger.exception("MAX data fetch error")
        if mid:
            await bot.edit_message(message_id=mid, text=f"❌ Ошибка: {e}")
    
    await bot.answer_callback(callback_id=c.callback_id)

@dp.callback_query(lambda c: c.payload == "select_date")
async def handle_select_date_btn(c: CallbackQuery):
    kb = build_max_date_keyboard(0)
    if c.message:
        await bot.edit_message(message_id=c.message.mid, text="Выберите дату:", keyboard=kb)
    await bot.answer_callback(callback_id=c.callback_id)

@dp.callback_query(lambda c: c.payload.startswith("toggle:"))
async def handle_toggle(c: CallbackQuery):
    parts = c.payload.split(":")
    mode = parts[1] # 'pair_N' or 'lang'
    lang = parts[2]
    date_str = parts[3]
    user_id = c.user.user_id
    mid = c.message.mid if c.message else None
    
    user_state = await state_manager.get_state(str(user_id))
    if not user_state:
        await bot.answer_callback(callback_id=c.callback_id, notification="Сессия истекла")
        return

    if mode == "lang":
        user_state["lang"] = "en" if lang == "ru" else "ru"
    elif mode.startswith("pair_"):
        selections = user_state.get("selections", {})
        selections[mode] = not selections.get(mode, True)
    
    await state_manager.set_state(str(user_id), user_state)
    
    if mid:
        await bot.edit_message(
            message_id=mid,
            text="Настройки обновлены:",
            keyboard=build_max_selection_keyboard(
                user_state["pairs"], user_state["selections"], user_state["lang"], date_str
            )
        )
    await bot.answer_callback(callback_id=c.callback_id)

@dp.callback_query(lambda c: c.payload.startswith("generate:"))
async def handle_generate(c: CallbackQuery):
    parts = c.payload.split(":")
    lang = parts[1]
    date_str = parts[2]
    user_id = c.user.user_id
    mid = c.message.mid if c.message else None
    
    user_state = await state_manager.get_state(str(user_id))
    if not user_state:
        await bot.answer_callback(callback_id=c.callback_id, notification="Ошибка сессии")
        return

    pairs = user_state.get("pairs", [])
    selections = user_state.get("selections", {})
    selected_pairs = [pairs[i] for i in range(len(pairs)) if selections.get(f"pair_{i}", True)]
    
    if not selected_pairs:
        await bot.answer_callback(callback_id=c.callback_id, notification="Выберите хоть что-то!")
        return

    if mid:
        await bot.edit_message(message_id=mid, text="⏳ Генерирую и отправляю PDF...")
    
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
            text=f"☦️ PDF на {get_date_human(date_str)} ({lang.upper()})",
            attachments=[File(token=media.token)]
        )
        if mid:
            await bot.edit_message(
                message_id=mid,
                text="✅ Готово! Можете выбрать другую дату:",
                keyboard=build_max_date_keyboard(0)
            )
    except Exception as e:
        logger.exception("MAX PDF error")
        if mid:
            await bot.edit_message(message_id=mid, text=f"❌ Ошибка генерации: {e}")
        
    await bot.answer_callback(callback_id=c.callback_id)

@app.post("/webhook/1f7c5225-1f1d-4c0c-b0b8-65a71b304b93")
async def max_webhook(request: Request, x_max_bot_api_secret: str = Header(None)):
    if x_max_bot_api_secret != MAX_SECRET:
        logger.warning(f"Invalid secret from {request.client.host}")
        raise HTTPException(status_code=403, detail="Invalid secret")
    
    update_data = await request.json()
    try:
        update = MaxoUpdate(update=bot.retort.load(update_data, Updates))
        await dp.feed_max_update(bot=bot, update=update)
    except Exception as e:
        logger.error(f"Error feeding update: {e}")
    return {"status": "ok"}
