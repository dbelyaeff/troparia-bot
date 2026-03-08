#!/usr/bin/env python3
"""
Telegram-бот: Тропари и Кондаки.

Генерирует PDF с тропарями и кондаками по Богослужебным указаниям.
Сцена выбора: показываем фрагмент указаний + пары тропарь/кондак с кнопками вкл/выкл.
"""

import io
import logging
import os
import re
from datetime import datetime, timedelta

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton,
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)

from generator import (
    fetch_troparia_kontakia, 
    fetch_page, 
    generate_pdf_bytes,
    parse_hours_from_ukazaniya,
)

logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Константы ───

DAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MONTHS_RU = {
    1: "янв", 2: "фев", 3: "мар", 4: "апр", 5: "мая", 6: "июн",
    7: "июл", 8: "авг", 9: "сен", 10: "окт", 11: "ноя", 12: "дек",
}
MONTHS_FULL = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}

FONT_PATH = os.environ.get("FONT_PATH", "/app/fonts/PonomarUnicode.otf")


# ─── Богослужебный день ───

def get_liturgical_date(reference_date: datetime = None) -> datetime:
    """
    Возвращает текущий богослужебный день.
    В православной традиции богослужебный день начинается с 17:00 предыдущего дня.
    """
    if reference_date is None:
        reference_date = datetime.now()
    
    if reference_date.hour >= 17:
        return reference_date + timedelta(days=1)
    return reference_date


def is_liturgical_today(date_str: str) -> bool:
    """Проверяет, является ли дата сегодняшним богослужебным днём."""
    target = datetime.strptime(date_str, "%Y-%m-%d").date()
    liturgical_today = get_liturgical_date().date()
    return target == liturgical_today


# ─── Клавиатура с датами ───

def build_date_keyboard(week_offset: int = 0) -> InlineKeyboardMarkup:
    """
    Строит inline-клавиатуру: 7 дней начиная с богослужебного today + week_offset*7.
    Первый день — богослужебный сегодня.
    Нельзя перейти в прошлое (кнопка "Назад" только для week_offset > 0).
    """
    today = datetime.now().date()
    liturgical_today = get_liturgical_date().date()
    
    # Начинаем с богослужебного сегодня
    start = liturgical_today + timedelta(weeks=week_offset)

    buttons = []
    
    # Для текущей недели (week_offset=0) — первая кнопка "Сегодня"
    if week_offset == 0:
        d = start
        weekday_full = "Воскресенье" if d.weekday() == 6 else DAYS_RU[d.weekday()]
        label = f"📅 Сегодня {d.day} {MONTHS_RU[d.month]} ({weekday_full})"
        buttons.append([InlineKeyboardButton(label, callback_data=f"date:{d.isoformat()}")])
        
        # Остальные 6 дней
        row = []
        for i in range(1, 7):
            d = start + timedelta(days=i)
            weekday = DAYS_RU[d.weekday()]
            
            # Воскресенье — широкой кнопкой
            if d.weekday() == 6:
                if row:
                    buttons.append(row)
                    row = []
                label = f"{d.day} {MONTHS_RU[d.month]} ({weekday}) ✝️"
                buttons.append([InlineKeyboardButton(label, callback_data=f"date:{d.isoformat()}")])
            else:
                label = f"{d.day} {MONTHS_RU[d.month]} ({weekday})"
                row.append(InlineKeyboardButton(label, callback_data=f"date:{d.isoformat()}"))
                if len(row) == 2:
                    buttons.append(row)
                    row = []
        if row:
            buttons.append(row)
    else:
        # Для других недель
        row = []
        for i in range(7):
            d = start + timedelta(days=i)
            weekday = DAYS_RU[d.weekday()]
            
            # Воскресенье — широкой кнопкой
            if d.weekday() == 6:
                if row:
                    buttons.append(row)
                    row = []
                label = f"{d.day} {MONTHS_RU[d.month]} ({weekday}) ✝️"
                buttons.append([InlineKeyboardButton(label, callback_data=f"date:{d.isoformat()}")])
            else:
                label = f"{d.day} {MONTHS_RU[d.month]} ({weekday})"
                row.append(InlineKeyboardButton(label, callback_data=f"date:{d.isoformat()}"))
                if len(row) == 2:
                    buttons.append(row)
                    row = []
        if row:
            buttons.append(row)

    # Навигация: "Назад" только если week_offset > 0 (нельзя уйти в прошлое)
    nav = []
    if week_offset > 0:
        nav.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"week:{week_offset - 1}"))
    nav.append(InlineKeyboardButton("Вперёд ▶️", callback_data=f"week:{week_offset + 1}"))
    buttons.append(nav)

    return InlineKeyboardMarkup(buttons)


# ─── Клавиатура выбора тропарей/кондаков ───

def build_selection_keyboard(pairs: list, selections: dict) -> InlineKeyboardMarkup:
    """
    Строит inline-клавиатуру для выбора пар тропарь+кондак.
    Каждая пара — отдельная кнопка с галочкой/крестиком.
    """
    buttons = []
    
    for pair_idx, pair in enumerate(pairs):
        pair_id = f"pair_{pair_idx}"
        is_selected = selections.get(pair_id, True)  # По умолчанию все выбраны
        check = "✅" if is_selected else "❌"
        
        # Формируем label: полное название + гласы
        section = pair.get("section", "")
        tropar_glas = pair.get("tropar_glas", "")
        kontak_glas = pair.get("kontak_glas", "")
        
        # Используем полное название без сокращений
        label = f"{check} {section} (гл.{tropar_glas}/{kontak_glas})"
        buttons.append([InlineKeyboardButton(label, callback_data=f"toggle:{pair_id}")])
    
    # Кнопка "Выбрать все / Снять все"
    if pairs:
        all_selected = all(selections.get(f"pair_{i}", True) for i in range(len(pairs)))
        toggle_all_text = "❌ Снять все" if all_selected else "✅ Выбрать все"
        buttons.append([InlineKeyboardButton(toggle_all_text, callback_data="toggle_all")])
    
    return InlineKeyboardMarkup(buttons)


def build_selection_reply_keyboard() -> ReplyKeyboardMarkup:
    """Создаёт reply-клавиатуру с кнопками в один ряд по две кнопки."""
    keyboard = [
        [KeyboardButton("📄 Сгенерировать PDF"), KeyboardButton("⬅️ Назад")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


# ─── Обработчики ───

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает выбор даты."""
    text = (
        "☦️ <b>Тропари и Кондаки на Часах</b>\n\n"
        "Бот генерирует PDF-документ с тропарями и кондаками\n"
        "для Часов (1-й, 3-й, 6-й, 9-й) согласно\n"
        "<b>Богослужебным указаниям</b>.\n\n"
        "Тексты берутся с сайта <b>azbyka.ru</b>.\n"
        "Документ форматируется шрифтом Ponomar Unicode\n"
        "в виде двух страниц на листе с рамками.\n\n"
        "📅 <b>Выберите дату:</b>"
    )
    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=build_date_keyboard(0),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает справку."""
    text = (
        "☦️ <b>Помощь</b>\n\n"
        "/start — показать выбор даты\n"
        "/help — эта справка\n\n"
        "<b>Как использовать:</b>\n"
        "1. Нажмите на дату в календаре\n"
        "2. Прочитайте фрагмент из Богослужебных указаний\n"
        "3. Выберите нужные тропари и кондаки\n"
        "   (нажимайте на кнопки — ✅/❌)\n"
        "4. Нажмите '📄 Сгенерировать PDF'\n\n"
        "<b>Богослужебный день</b>\n"
        "Начинается с 17:00 предыдущего дня"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает inline-нажатия."""
    query = update.callback_query
    await query.answer()
    data = query.data

    # ─── Пагинация недель ───
    if data.startswith("week:"):
        offset = int(data.split(":")[1])
        await query.edit_message_reply_markup(
            reply_markup=build_date_keyboard(offset)
        )
        return

    # ─── Выбор даты ───
    if data.startswith("date:"):
        date_str = data.split(":")[1]
        
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        date_human = f"{dt.day} {MONTHS_FULL[dt.month]} {dt.year} г."
        
        await query.edit_message_text(
            f"⏳ Загружаю Богослужебные указания на <b>{date_human}</b>...",
            parse_mode="HTML",
        )
        
        try:
            # 1. Загружаем богослужебные указания
            ukazaniya_html = fetch_page(f"https://azbyka.ru/bogosluzhebnye-ukazaniya?date={date_str}")
            
            # 2. Парсим, что читается на часах
            hours_info = parse_hours_from_ukazaniya(ukazaniya_html)
            
            # 3. Загружаем календарь для получения текстов
            all_troparia = fetch_troparia_kontakia(date_str)
            
            # 4. Сопоставляем указания с текстами — создаём пары
            pairs = []
            seen_tropars = set()  # Для отслеживания дубликатов
            seen_kontaks = set()
            
            # Собираем все уникальные тропари/кондаки из указаний
            all_refs = {"tropars": set(), "kontaks": set()}
            
            for info in hours_info:
                raw_text = info.get("raw_text", "")
                
                # Извлекаем тропари из текста
                if "тропарь воскресный" in raw_text.lower():
                    all_refs["tropars"].add(("воскресный", None))
                if "тропарь триоди" in raw_text.lower() or "тропарь креста" in raw_text.lower():
                    all_refs["tropars"].add(("триодь", None))
                if "тропарь богородиц" in raw_text.lower() or "иконы" in raw_text.lower():
                    all_refs["tropars"].add(("богородица", None))
                if "григор" in raw_text.lower():
                    glas_match = re.search(r'глас\s*(\d+)[-:]?', raw_text.lower())
                    glas = glas_match.group(1) if glas_match else None
                    all_refs["tropars"].add(("григорий", glas))
                
                # Извлекаем кондаки из текста
                if "кондак воскресный" in raw_text.lower():
                    all_refs["kontaks"].add(("воскресный", None))
                if "кондак триоди" in raw_text.lower() or "кондак креста" in raw_text.lower():
                    all_refs["kontaks"].add(("триодь", None))
                if "кондак богородиц" in raw_text.lower():
                    all_refs["kontaks"].add(("богородица", None))
                if "григор" in raw_text.lower() and "кондак" in raw_text.lower():
                    glas_match = re.search(r'кондак.*?глас\s*(\d+)[-:]?', raw_text.lower())
                    glas = glas_match.group(1) if glas_match else None
                    all_refs["kontaks"].add(("григорий", glas))
            
            # Теперь ищем тексты для каждого референса
            for ref, glas in all_refs["tropars"]:
                tropar_text = None
                tropar_glas = ""
                
                for sec in all_troparia:
                    sec_name = sec.get("section", "")
                    # Ищем совпадения по названию
                    match = False
                    if ref == "воскресный" and "воскресн" in sec_name.lower():
                        match = True
                    elif ref == "триодь" and ("триод" in sec_name.lower() or "пост" in sec_name.lower() or "крест" in sec_name.lower()):
                        match = True
                    elif ref == "богородица" and ("богородиц" in sec_name.lower() or "икон" in sec_name.lower()):
                        match = True
                    elif ref == "григорий" and "григор" in sec_name.lower():
                        match = True
                    
                    if match:
                        items = sec.get("items", [])
                        for item in items:
                            if item["type"] == "Тропарь" and not tropar_text:
                                if glas is None or item["glas"] == glas:
                                    tropar_text = item["text"]
                                    tropar_glas = item["glas"]
                                    break
                
                if tropar_text:
                    tropar_key = (tropar_text[:50], tropar_glas)
                    if tropar_key not in seen_tropars:
                        seen_tropars.add(tropar_key)
                        # Capitalize the ref name
                        ref_display = ref.capitalize() if ref != "триодь" else "Триоди"
                        pairs.append({
                            "section": f"Тропарь ({ref_display})",
                            "tropar": {"text": tropar_text, "glas": tropar_glas},
                            "kontak": None,
                            "tropar_glas": tropar_glas,
                            "kontak_glas": "",
                        })
            
            for ref, glas in all_refs["kontaks"]:
                kontak_text = None
                kontak_glas = ""
                
                for sec in all_troparia:
                    sec_name = sec.get("section", "")
                    # Ищем совпадения по названию
                    match = False
                    if ref == "воскресный" and "воскресн" in sec_name.lower():
                        match = True
                    elif ref == "триодь" and ("триод" in sec_name.lower() or "пост" in sec_name.lower() or "крест" in sec_name.lower()):
                        match = True
                    elif ref == "богородица" and ("богородиц" in sec_name.lower() or "икон" in sec_name.lower()):
                        match = True
                    elif ref == "григорий" and "григор" in sec_name.lower():
                        match = True
                    
                    if match:
                        items = sec.get("items", [])
                        for item in items:
                            if item["type"] == "Кондак" and not kontak_text:
                                if glas is None or item["glas"] == glas:
                                    kontak_text = item["text"]
                                    kontak_glas = item["glas"]
                                    break
                
                if kontak_text:
                    kontak_key = (kontak_text[:50], kontak_glas)
                    if kontak_key not in seen_kontaks:
                        seen_kontaks.add(kontak_key)
                        # Capitalize the ref name
                        ref_display = ref.capitalize() if ref != "триодь" else "Триоди"
                        pairs.append({
                            "section": f"Кондак ({ref_display}, глас {kontak_glas})",
                            "tropar": None,
                            "kontak": {"text": kontak_text, "glas": kontak_glas},
                            "tropar_glas": "",
                            "kontak_glas": kontak_glas,
                        })
            
            # Если не нашли указаний, берём все тропари/кондаки дня
            if not pairs:
                for sec in all_troparia:
                    section_name = sec.get("section", "")
                    items = sec.get("items", [])
                    troparia = [i for i in items if i["type"] == "Тропарь"]
                    kontakia = [i for i in items if i["type"] == "Кондак"]
                    
                    for t_idx, tropar in enumerate(troparia):
                        kontak = kontakia[t_idx] if t_idx < len(kontakia) else None
                        pairs.append({
                            "section": section_name,
                            "tropar": tropar,
                            "kontak": kontak,
                            "tropar_glas": tropar["glas"],
                            "kontak_glas": kontak["glas"] if kontak else "-",
                        })
            
            if not pairs:
                raise ValueError("Не найдены тропари/кондаки")
            
            context.user_data["selected_date"] = date_str
            context.user_data["pairs"] = pairs
            context.user_data["selections"] = {f"pair_{i}": True for i in range(len(pairs))}
            
            # Формируем текст указаний
            ukazaniya_text = "\n\n".join([info.get("raw_text", "") for info in hours_info]) if hours_info else "Все тропари и кондаки дня"
            context.user_data["ukazaniya_text"] = ukazaniya_text
            
            # Первое сообщение: фрагмент указаний
            ukazaniya_msg = (
                f"📖 <b>Богослужебные указания</b>\n"
                f"на <b>{date_human}</b>\n\n"
                f"<i>Что читается на Часах:</i>\n\n"
                f"{ukazaniya_text}"
            )
            
            await query.edit_message_text(
                ukazaniya_msg,
                parse_mode="HTML",
            )
            
            # Второе сообщение: список пар с кнопками
            selection_msg = (
                f"🔹 <b>Выберите тропари и кондаки</b>\n\n"
                f"Найдено пар: {len(pairs)}\n"
                f"Нажимайте на кнопки ниже, чтобы включить/выключить:\n\n"
                f"✅ — будет в PDF\n"
                f"❌ — не будет в PDF\n\n"
                f"Или используйте кнопки внизу:"
            )

            await query.message.reply_text(
                selection_msg,
                parse_mode="HTML",
                reply_markup=build_selection_keyboard(pairs, context.user_data["selections"])
            )
            
            # Отправляем Reply-клавиатуру
            await query.message.reply_text(
                "Выбирайте тропари и кондаки 👆",
                reply_markup=build_selection_reply_keyboard()
            )
            
        except Exception as e:
            logger.exception("Ошибка загрузки данных")
            await query.message.reply_text(
                f"❌ Ошибка при загрузке:\n<code>{e}</code>\n\n"
                "Попробуйте другую дату.",
                parse_mode="HTML",
                reply_markup=build_date_keyboard(0),
            )
        return

    # ─── Переключение выбора (toggle) ───
    if data.startswith("toggle:"):
        pair_id = data.split(":")[1]
        selections = context.user_data.get("selections", {})
        
        current = selections.get(pair_id, True)
        selections[pair_id] = not current
        context.user_data["selections"] = selections
        
        pairs = context.user_data.get("pairs", [])
        await query.edit_message_reply_markup(
            reply_markup=build_selection_keyboard(pairs, selections)
        )
        return

    # ─── Выбрать все / Снять все ───
    if data == "toggle_all":
        pairs = context.user_data.get("pairs", [])
        selections = context.user_data.get("selections", {})
        
        all_selected = all(selections.get(f"pair_{i}", True) for i in range(len(pairs)))
        
        for i in range(len(pairs)):
            selections[f"pair_{i}"] = not all_selected
        
        context.user_data["selections"] = selections
        
        await query.edit_message_reply_markup(
            reply_markup=build_selection_keyboard(pairs, selections)
        )
        return

    # ─── Сгенерировать PDF ───
    if data == "generate_pdf":
        date_str = context.user_data.get("selected_date")
        pairs = context.user_data.get("pairs", [])
        selections = context.user_data.get("selections", {})
        
        if not date_str or not pairs:
            await query.message.reply_text(
                "❌ Сначала выберите дату из календаря.",
                reply_markup=build_date_keyboard(0)
            )
            return
        
        # Фильтруем выбранные пары
        selected_pairs = [
            pairs[i] for i in range(len(pairs))
            if selections.get(f"pair_{i}", True)
        ]
        
        if not selected_pairs:
            await query.message.reply_text(
                "❌ Выберите хотя бы одну пару тропарь+кондак!"
            )
            return
        
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        date_human = f"{dt.day} {MONTHS_FULL[dt.month]} {dt.year} г."
        today_mark = ""
        
        await query.edit_message_text(
            f"⏳ Генерирую PDF на <b>{date_human}{today_mark}</b>...\n"
            f"Выбрано пар: {len(selected_pairs)}",
            parse_mode="HTML",
        )
        
        try:
            # Преобразуем пары в секции для генератора
            sections = []
            for pair in selected_pairs:
                items = []
                if pair.get("tropar"):
                    items.append({
                        "type": "Тропарь",
                        "glas": pair["tropar_glas"],
                        "text": pair["tropar"]["text"]
                    })
                if pair.get("kontak"):
                    items.append({
                        "type": "Кондак",
                        "glas": pair["kontak_glas"],
                        "text": pair["kontak"]["text"]
                    })
                if items:
                    sections.append({
                        "section": pair["section"],
                        "items": items
                    })
            
            pdf_bytes = generate_pdf_bytes(
                date_str, 
                FONT_PATH, 
                sections=sections
            )
            
            filename = f"Тропари_и_кондаки_{date_str}.pdf"
            
            await query.message.reply_document(
                document=io.BytesIO(pdf_bytes),
                filename=filename,
                caption=f"☦️ Тропари и кондаки на Часах ({date_human}{today_mark})"
            )
            
            # Возвращаем выбор даты
            await query.message.reply_text(
                "📅 <b>Выберите следующую дату:</b>",
                parse_mode="HTML",
                reply_markup=build_date_keyboard(0)
            )
            
        except Exception as e:
            logger.exception("Ошибка генерации PDF")
            await query.message.reply_text(
                f"❌ Ошибка при генерации:\n<code>{e}</code>\n\n"
                "Попробуйте другую дату.",
                parse_mode="HTML",
                reply_markup=build_date_keyboard(0),
            )
        return

    # ─── Назад к календарю ───
    if data == "back_to_calendar":
        # Очищаем контекст
        context.user_data.pop("selected_date", None)
        context.user_data.pop("pairs", None)
        context.user_data.pop("selections", None)
        context.user_data.pop("ukazaniya_text", None)

        await query.edit_message_text(
            "📅 <b>Выберите дату:</b>",
            parse_mode="HTML",
            reply_markup=build_date_keyboard(0)
        )

        # Удаляем сообщение с выбором
        try:
            await query.message.delete()
        except:
            pass
        
        # Убираем Reply-клавиатуру
        from telegram import ReplyKeyboardRemove
        await query.message.reply_text(
            ".",
            reply_markup=ReplyKeyboardRemove()
        )

        return


async def reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает reply-кнопки."""
    text = update.message.text
    
    # ─── Сгенерировать PDF ───
    if text == "📄 Сгенерировать PDF":
        # Вызываем логику генерации через callback
        date_str = context.user_data.get("selected_date")
        pairs = context.user_data.get("pairs", [])
        selections = context.user_data.get("selections", {})
        
        if not date_str or not pairs:
            await update.message.reply_text(
                "❌ Сначала выберите дату из календаря.",
                reply_markup=build_date_keyboard(0)
            )
            return
        
        # Фильтруем выбранные пары
        selected_pairs = [
            pairs[i] for i in range(len(pairs))
            if selections.get(f"pair_{i}", True)
        ]
        
        if not selected_pairs:
            await update.message.reply_text(
                "❌ Выберите хотя бы одну пару тропарь+кондак!"
            )
            return
        
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        date_human = f"{dt.day} {MONTHS_FULL[dt.month]} {dt.year} г."
        
        await update.message.reply_text(
            f"⏳ Генерирую PDF на <b>{date_human}</b>...\n"
            f"Выбрано пар: {len(selected_pairs)}",
            parse_mode="HTML",
        )
        
        try:
            # Преобразуем пары в секции для генератора
            sections = []
            for pair in selected_pairs:
                items = []
                if pair.get("tropar"):
                    items.append({
                        "type": "Тропарь",
                        "glas": pair["tropar_glas"],
                        "text": pair["tropar"]["text"]
                    })
                if pair.get("kontak"):
                    items.append({
                        "type": "Кондак",
                        "glas": pair["kontak_glas"],
                        "text": pair["kontak"]["text"]
                    })
                if items:
                    sections.append({
                        "section": pair["section"],
                        "items": items
                    })
            
            pdf_bytes = generate_pdf_bytes(
                date_str, 
                FONT_PATH, 
                sections=sections
            )
            
            filename = f"Тропари_и_кондаки_{date_str}.pdf"
            
            await update.message.reply_document(
                document=io.BytesIO(pdf_bytes),
                filename=filename,
                caption=f"☦️ Тропари и кондаки на Часах ({date_human})"
            )

            # Возвращаем выбор даты
            await update.message.reply_text(
                "📅 <b>Выберите следующую дату:</b>",
                parse_mode="HTML",
                reply_markup=build_date_keyboard(0)
            )
            
            # Убираем Reply-клавиатуру
            from telegram import ReplyKeyboardRemove
            await update.message.reply_text(
                ".",
                reply_markup=ReplyKeyboardRemove()
            )
            
        except Exception as e:
            logger.exception("Ошибка генерации PDF")
            await update.message.reply_text(
                f"❌ Ошибка при генерации:\n<code>{e}</code>\n\n"
                "Попробуйте другую дату.",
                parse_mode="HTML",
                reply_markup=build_date_keyboard(0),
            )
            # Убираем Reply-клавиатуру
            from telegram import ReplyKeyboardRemove
            await update.message.reply_text(
                ".",
                reply_markup=ReplyKeyboardRemove()
            )
        return
    
    # ─── Назад к календарю ───
    if text == "⬅️ Назад":
        # Очищаем контекст
        context.user_data.pop("selected_date", None)
        context.user_data.pop("pairs", None)
        context.user_data.pop("selections", None)
        context.user_data.pop("ukazaniya_text", None)
        
        await update.message.reply_text(
            "📅 <b>Выберите дату:</b>",
            parse_mode="HTML",
            reply_markup=build_date_keyboard(0)
        )
        
        # Убираем Reply-клавиатуру
        from telegram import ReplyKeyboardRemove
        await update.message.reply_text(
            ".",
            reply_markup=ReplyKeyboardRemove()
        )
        return


# ─── Запуск ───

def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN не задан! Укажите в .env или переменных окружения.")
        return

    if not os.path.exists(FONT_PATH):
        logger.error(f"Шрифт не найден: {FONT_PATH}")
        return

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply_handler))

    logger.info("🤖 Бот запущен (Long Polling)")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
