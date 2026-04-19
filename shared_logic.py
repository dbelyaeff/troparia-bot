import re
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional

from generator import (
    fetch_troparia_kontakia,
    fetch_page,
    parse_hours_from_ukazaniya,
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

def get_liturgical_date(reference_date: datetime = None) -> datetime:
    if reference_date is None:
        reference_date = datetime.now()
    if reference_date.hour >= 17:
        return reference_date + timedelta(days=1)
    return reference_date

def get_date_human(date_str: str) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{dt.day} {MONTHS_FULL[dt.month]} {dt.year} г."

async def fetch_data_for_date(date_str: str) -> Tuple[str, List[Dict]]:
    """Загружает указания и пары тропарей/кондаков."""
    # 1. Загружаем богослужебные указания
    ukazaniya_html = fetch_page(f"https://azbyka.ru/bogosluzhebnye-ukazaniya?date={date_str}")
    hours_info = parse_hours_from_ukazaniya(ukazaniya_html)
    
    # 2. Загружаем календарь для получения текстов
    all_troparia = fetch_troparia_kontakia(date_str)
    
    # 3. Сопоставляем указания с текстами
    pairs = []
    seen_tropars = set()
    seen_kontaks = set()
    all_refs = {"tropars": set(), "kontaks": set()}
    
    for info in hours_info:
        raw_text = info.get("raw_text", "").lower()
        # Извлекаем ссылки на тропари/кондаки из текста
        if "тропарь воскресный" in raw_text:
            all_refs["tropars"].add(("воскресный", None))
        if any(kw in raw_text for kw in ["тропарь триоди", "тропарь цветной", "тропарь праздника", "тропарь пасхи"]):
            all_refs["tropars"].add(("праздник", None))
        if any(kw in raw_text for kw in ["тропарь свято", "тропарь преподобн", "тропарь мученик", "тропарь святител", "тропарь блж", "тропарь прав"]):
            all_refs["tropars"].add(("святой", None))
        if "тропарь богородиц" in raw_text or "иконы" in raw_text:
            all_refs["tropars"].add(("богородица", None))
        if "григор" in raw_text:
            glas_match = re.search(r'глас\s*(\d+)[-:]?', raw_text)
            glas = glas_match.group(1) if glas_match else None
            all_refs["tropars"].add(("григорий", glas))
            
        if "кондак воскресный" in raw_text:
            all_refs["kontaks"].add(("воскресный", None))
        if any(kw in raw_text for kw in ["кондак триоди", "кондак цветной", "кондак праздника", "кондак пасхи"]):
            all_refs["kontaks"].add(("праздник", None))
        if any(kw in raw_text for kw in ["кондак свято", "кондак преподобн", "кондак мученик", "кондак святител", "кондак блж", "кондак прав"]):
            all_refs["kontaks"].add(("святой", None))
        if "кондак богородиц" in raw_text:
            all_refs["kontaks"].add(("богородица", None))
        if "григор" in raw_text and "кондак" in raw_text:
            glas_match = re.search(r'кондак.*?глас\s*(\d+)[-:]?', raw_text)
            glas = glas_match.group(1) if glas_match else None
            all_refs["kontaks"].add(("григорий", glas))

    # Сбор тропарей
    for ref, glas in all_refs["tropars"]:
        for sec in all_troparia:
            sec_name = sec.get("section", "")
            match = False
            s_low = sec_name.lower()
            if ref == "воскресный" and "воскресн" in s_low: match = True
            elif ref == "праздник" and any(kw in s_low for kw in ["триод", "цвето", "недел", "пасх", "праздник", "успени", "рождеств", "покров"]): match = True
            elif ref == "святой" and not any(kw in s_low for kw in ["воскресн", "пасх", "триод", "богородиц", "икон"]): match = True
            elif ref == "богородица" and ("богородиц" in s_low or "икон" in s_low): match = True
            elif ref == "григорий" and "григор" in s_low: match = True
            
            if match:
                for item in sec.get("items", []):
                    if item["type"] == "Тропарь":
                        if glas is None or item["glas"] == glas:
                            t_text, t_glas = item["text"], item["glas"]
                            key = (t_text[:50], t_glas)
                            if key not in seen_tropars:
                                seen_tropars.add(key)
                                pairs.append({
                                    "section": sec_name,
                                    "type": item.get("display_type", "Тропарь"),
                                    "tropar": {"text": t_text, "glas": t_glas},
                                    "kontak": None,
                                    "tropar_glas": t_glas,
                                    "kontak_glas": "",
                                })

    # Дополнительный поиск: ищем упоминания всех разделов в полном тексте указаний
    # Это поможет найти святых, которые не попали в краткий список "на часах"
    uk_low = ukazaniya_html.lower()
    for sec in all_troparia:
        sec_name = sec.get("section", "")
        if "воскресн" in sec_name.lower(): continue # Уже обработано
        
        # Извлекаем значимые слова (дольше 3 символов)
        words = re.findall(r'[а-яА-ЯёЁ]{4,}', sec_name)
        if not words: continue
        
        found = False
        # Проверяем первые два значимых слова (обычно имя или название праздника)
        for w in words[:2]:
            # Отрезаем окончание для более гибкого поиска (оставляем корень)
            root = w[:-2] if len(w) > 5 else w[:-1]
            if root.lower() in uk_low:
                found = True
                break
        
        if found:
            for item in sec.get("items", []):
                seen_set = seen_tropars if item["type"] == "Тропарь" else seen_kontaks
                key = (item["text"][:50], item["glas"])
                if key not in seen_set:
                    seen_set.add(key)
                    pairs.append({
                        "section": sec_name,
                        "type": item.get("display_type", item["type"]),
                        "tropar": item if item["type"] == "Тропарь" else None,
                        "kontak": item if item["type"] == "Кондак" else None,
                        "tropar_glas": item["glas"] if item["type"] == "Тропарь" else "",
                        "kontak_glas": item["glas"] if item["type"] == "Кондак" else "",
                    })

    # Сбор кондаков
    for ref, glas in all_refs["kontaks"]:
        for sec in all_troparia:
            sec_name = sec.get("section", "")
            match = False
            s_low = sec_name.lower()
            if ref == "воскресный" and "воскресн" in s_low: match = True
            elif ref == "праздник" and any(kw in s_low for kw in ["триод", "цвето", "недел", "пасх", "праздник", "успени", "рождеств", "покров"]): match = True
            elif ref == "святой" and not any(kw in s_low for kw in ["воскресн", "пасх", "триод", "богородиц", "икон"]): match = True
            elif ref == "богородица" and ("богородиц" in s_low or "икон" in s_low): match = True
            elif ref == "григорий" and "григор" in s_low: match = True
            
            if match:
                for item in sec.get("items", []):
                    if item["type"] == "Кондак":
                        if glas is None or item["glas"] == glas:
                            k_text, k_glas = item["text"], item["glas"]
                            key = (k_text[:50], k_glas)
                            if key not in seen_kontaks:
                                seen_kontaks.add(key)
                                pairs.append({
                                    "section": sec_name,
                                    "type": item.get("display_type", "Кондак"),
                                    "tropar": None,
                                    "kontak": {"text": k_text, "glas": k_glas},
                                    "tropar_glas": "",
                                    "kontak_glas": k_glas,
                                })

    if not pairs:
        for sec in all_troparia:
            section_name = sec.get("section", "")
            items = sec.get("items", [])
            troparia = [i for i in items if i["type"] == "Тропарь"]
            kontakia = [i for i in items if i["type"] == "Кондак"]
            # Пытаемся объединить в пары, если их одинаковое количество
            if len(troparia) == len(kontakia) and len(troparia) > 0:
                for t, k in zip(troparia, kontakia):
                    pairs.append({
                        "section": section_name,
                        "type": "Тропарь и кондак",
                        "tropar": t,
                        "kontak": k,
                        "tropar_glas": t["glas"],
                        "kontak_glas": k["glas"],
                    })
            else:
                for t in troparia:
                    pairs.append({
                        "section": section_name,
                        "type": t.get("display_type", "Тропарь"),
                        "tropar": t, "kontak": None,
                        "tropar_glas": t["glas"], "kontak_glas": "",
                    })
                for k in kontakia:
                    pairs.append({
                        "section": section_name,
                        "type": k.get("display_type", "Кондак"),
                        "tropar": None, "kontak": k,
                        "tropar_glas": "", "kontak_glas": k["glas"],
                    })

    ukazaniya_text = "\n\n".join([info.get("raw_text", "") for info in hours_info]) if hours_info else "Все тропари и кондаки дня"
    return ukazaniya_text, pairs

def get_pdf_sections(selected_pairs: List[Dict]) -> List[Dict]:
    """Преобразует выбранные пары в секции для генератора PDF."""
    sections = []
    for pair in selected_pairs:
        items = []
        if pair.get("tropar"):
            p_type = pair.get("type", "Тропарь")
            if "кондак" in p_type.lower() and pair.get("kontak") is None: # safety check
                p_type = "Тропарь"
            items.append({"type": p_type, "glas": pair["tropar_glas"], "text": pair["tropar"]["text"]})
        if pair.get("kontak"):
            p_type = pair.get("type", "Кондак")
            if "тропарь" in p_type.lower() and pair.get("tropar") is None: # safety check
                 p_type = "Кондак"
            items.append({"type": p_type, "glas": pair["kontak_glas"], "text": pair["kontak"]["text"]})
        if items:
            sections.append({"section": pair["section"], "items": items})
    return sections
