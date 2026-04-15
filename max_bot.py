import io
import logging
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from contextlib import asynccontextmanager

from maxapi import Bot, Dispatcher
from maxapi.methods.types.getted_updates import process_update_webhook
from maxapi.types import MessageCreated, MessageCallback, InputMediaBuffer, CallbackButton
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder
from maxapi.enums.parse_mode import ParseMode
# ─── Monkeypatch maxapi ───
# The library uses deprecated access_token query param and wrong default URL.
# We fix it here to avoid maintainance overhead of a local fork.
import maxapi.connection.base
import maxapi.bot

_original_request = maxapi.connection.base.BaseConnection.request

async def _patched_request(self, method, path, model=None, is_return_raw=False, **kwargs):
    # Ensure headers include Authorization: <token>
    headers = kwargs.get('headers', {})
    # maxapi.Bot stores token in self.__token -> _Bot__token
    token = getattr(self.bot, '_Bot__token', None) if self.bot else None
    if token:
        headers['Authorization'] = token
    kwargs['headers'] = headers
    
    # Remove deprecated access_token from query params
    params = kwargs.get('params', {})
    if isinstance(params, dict) and 'access_token' in params:
        params = params.copy()
        del params['access_token']
    kwargs['params'] = params
    
    return await _original_request(self, method, path, model, is_return_raw, **kwargs)

maxapi.connection.base.BaseConnection.request = _patched_request

from magic_filter import F

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

# Use the correct platform-api domain and enable HTML parsing
bot = Bot(token=MAX_TOKEN, parse_mode=ParseMode.HTML)
bot.API_URL = 'https://platform-api.max.ru'
dp = Dispatcher()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure dispatcher is ready
    dp.bot = bot
    if dp not in dp.routers:
        dp.routers.append(dp)
        
    # Startup: register webhook
    webhook_url = os.environ.get("MAX_WEBHOOK_URL")
    if webhook_url:
        try:
            logger.info(f"Starting bot and registering MAX webhook: {webhook_url}")
            # Use subscribe_webhook instead of subscribe
            res = await bot.subscribe_webhook(url=webhook_url, secret=MAX_SECRET)
            logger.info(f"MAX webhook registration response: {res}")
        except Exception as e:
            logger.error(f"Failed to register MAX webhook: {e}")
    yield

app = FastAPI(lifespan=lifespan)

# ─── Клавиатуры ───

def build_max_date_keyboard(week_offset: int = 0):
    liturgical_today = get_liturgical_date().date()
    start_of_week = liturgical_today + timedelta(weeks=week_offset)
    builder = InlineKeyboardBuilder()
    
    # Navigation row
    builder.row(
        CallbackButton(text="◀️", payload=f"date_week_{week_offset - 1}"),
        CallbackButton(text="▶️", payload=f"date_week_{week_offset + 1}")
    )
    
    # Date buttons
    for i in range(7):
        date = start_of_week + timedelta(days=i)
        date_str = date.strftime("%Y-%m-%d")
        weekday = DAYS_RU[date.weekday()]
        label = f"{date.day} {MONTHS_RU[date.month]} ({weekday})"
        if date == liturgical_today:
            label = f"📅 {label}"
        builder.row(CallbackButton(text=label, payload=f"date_select_{date_str}"))
        
    return builder.as_markup()

def build_max_selection_keyboard(pairs: list, selections: dict, lang: str = "ru", date_str: str = ""):
    builder = InlineKeyboardBuilder()
    
    # Display toggle for each pair
    for i, pair in enumerate(pairs):
        pair_id = f"pair_{i}"
        is_selected = selections.get(pair_id, True)
        icon = "✅" if is_selected else "⬜"
        builder.row(CallbackButton(
            text=f"{icon} {pair.get('section', '')}", 
            payload=f"toggle:{pair_id}:{lang}:{date_str}"
        ))

    # Language and Actions row
    builder.row(CallbackButton(text=f"🌐 Язык: {lang.upper()}", payload=f"toggle:lang:{lang}:{date_str}"))
    builder.row(CallbackButton(text="📄 Сгенерировать PDF", payload=f"generate:{lang}:{date_str}"))
    builder.row(CallbackButton(text="⬅️ К календарю", payload="select_date"))
    
    return builder.as_markup()

# ─── Обработчики ───

@dp.message_created(F.message.body.text == "/start")
async def handle_start(event: MessageCreated):
    logger.info(f"handle_start: user_id={event.message.sender.user_id}")
    user_id = event.message.sender.user_id
    await state_manager.clear_state(str(user_id))
    await event.message.answer(
         text="☦️ <b>Тропари и Кондаки</b>\n\nВыберите дату:",
         attachments=[build_max_date_keyboard(0)]
    )

@dp.message_callback(F.callback.payload.startswith("date_week_"))
async def handle_date_nav(event: MessageCallback):
    logger.info(f"handle_date_nav: {event.callback.payload}")
    week_offset = int(event.callback.payload.split("_")[2])
    kb = build_max_date_keyboard(week_offset)
    
    await bot.edit_message(
        chat_id=event.message.recipient.chat_id,
        message_id=event.message.body.mid,
        text="Выберите дату:",
        attachments=[kb]
    )
    await bot.send_callback(callback_id=event.callback.callback_id)

@dp.message_callback(F.callback.payload.startswith("date_select_"))
async def handle_date_select(event: MessageCallback):
    logger.info(f"handle_date_select: {event.callback.payload}")
    parts = event.callback.payload.split("_")
    if len(parts) < 3:
        logger.error(f"Invalid payload format: {event.callback.payload}")
        return
    date_str = parts[2]
    user_id = event.callback.user.user_id
    mid = event.message.body.mid
    chat_id = event.message.recipient.chat_id
    
    await bot.edit_message(chat_id=chat_id, message_id=mid, text="⏳ Загружаю данные...")
    
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
        await bot.edit_message(
            chat_id=chat_id,
            message_id=mid,
            text=text,
            attachments=[build_max_selection_keyboard(pairs, user_state["selections"], "ru", date_str)]
        )
    except Exception as e:
        logger.exception("MAX data fetch error")
        await bot.edit_message(chat_id=chat_id, message_id=mid, text=f"❌ Ошибка: {e}")
    
    await bot.send_callback(callback_id=event.callback.callback_id)

@dp.message_callback(F.callback.payload == "select_date")
async def handle_select_date_btn(event: MessageCallback):
    logger.info("handle_select_date_btn")
    kb = build_max_date_keyboard(0)
    await bot.edit_message(
        chat_id=event.message.recipient.chat_id,
        message_id=event.message.body.mid,
        text="Выберите дату:",
        attachments=[kb]
    )
    await bot.send_callback(callback_id=event.callback.callback_id)

@dp.message_callback(F.callback.payload.startswith("toggle:"))
async def handle_toggle(event: MessageCallback):
    logger.info(f"handle_toggle: {event.callback.payload}")
    parts = event.callback.payload.split(":")
    mode = parts[1] # 'pair_N' or 'lang'
    lang = parts[2]
    date_str = parts[3]
    user_id = event.callback.user.user_id
    mid = event.message.body.mid
    chat_id = event.message.recipient.chat_id
    
    user_state = await state_manager.get_state(str(user_id))
    if not user_state:
        await bot.send_callback(callback_id=event.callback.callback_id, notification="Сессия истекла")
        return

    if mode == "lang":
        user_state["lang"] = "en" if lang == "ru" else "ru"
    elif mode.startswith("pair_"):
        selections = user_state.get("selections", {})
        selections[mode] = not selections.get(mode, True)
    
    await state_manager.set_state(str(user_id), user_state)
    
    await bot.edit_message(
        chat_id=chat_id,
        message_id=mid,
        text="Настройки обновлены:",
        attachments=[build_max_selection_keyboard(
            user_state["pairs"], user_state["selections"], user_state["lang"], date_str
        )]
    )
    await bot.send_callback(callback_id=event.callback.callback_id)

@dp.message_callback(F.callback.payload.startswith("generate:"))
async def handle_generate(event: MessageCallback):
    logger.info(f"handle_generate: {event.callback.payload}")
    parts = event.callback.payload.split(":")
    lang = parts[1]
    date_str = parts[2]
    user_id = event.callback.user.user_id
    mid = event.message.body.mid
    chat_id = event.message.recipient.chat_id
    
    user_state = await state_manager.get_state(str(user_id))
    if not user_state:
        await bot.send_callback(callback_id=event.callback.callback_id, notification="Ошибка сессии")
        return

    pairs = user_state.get("pairs", [])
    selections = user_state.get("selections", {})
    selected_pairs = [pairs[i] for i in range(len(pairs)) if selections.get(f"pair_{i}", True)]
    
    if not selected_pairs:
        await bot.send_callback(callback_id=event.callback.callback_id, notification="Выберите хоть что-то!")
        return

    await bot.edit_message(chat_id=chat_id, message_id=mid, text="⏳ Генерирую и отправляю PDF...")
    
    try:
        pdf_bytes = generate_pdf_bytes(date_str, FONT_PATH, sections=get_pdf_sections(selected_pairs))
        
        attachment = await bot.upload_file_buffer(buffer=pdf_bytes, filename=f"Troparia_{date_str}.pdf")
        
        await bot.send_message(
            chat_id=chat_id,
            text=f"☦️ PDF на {get_date_human(date_str)} ({lang.upper()})",
            attachments=[attachment]
        )
        await bot.edit_message(
            chat_id=chat_id,
            message_id=mid,
            text="✅ Готово! Можете выбрать другую дату:",
            attachments=[build_max_date_keyboard(0)]
        )
    except Exception as e:
        logger.exception("MAX PDF error")
        await bot.edit_message(chat_id=chat_id, message_id=mid, text=f"❌ Ошибка генерации: {e}")
        
    await bot.send_callback(callback_id=event.callback.callback_id)

@app.post("/webhook/1f7c5225-1f1d-4c0c-b0b8-65a71b304b93")
async def max_webhook(request: Request, x_max_bot_api_secret: str = Header(None)):
    if x_max_bot_api_secret != MAX_SECRET:
        logger.warning(f"Invalid secret from {request.client.host}")
        # raise HTTPException(status_code=403, detail="Invalid secret")
    
    update_data = await request.json()
    try:
        event = await process_update_webhook(event_json=update_data, bot=bot)
        if event:
            await dp.handle(event)
    except Exception as e:
        logger.error(f"Error feeding update: {e}")
    return {"status": "ok"}
