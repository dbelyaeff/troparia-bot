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
        raw_text = info.get("raw_text", "")
        # Извлекаем ссылки на тропари/кондаки из текста
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

    # Сбор пар
    for ref, glas in all_refs["tropars"]:
        tropar_text = None
        tropar_glas = ""
        for sec in all_troparia:
            sec_name = sec.get("section", "")
            match = False
            if ref == "воскресный" and "воскресн" in sec_name.lower(): match = True
            elif ref == "триодь" and ("триод" in sec_name.lower() or "пост" in sec_name.lower() or "крест" in sec_name.lower()): match = True
            elif ref == "богородица" and ("богородиц" in sec_name.lower() or "икон" in sec_name.lower()): match = True
            elif ref == "григорий" and "григор" in sec_name.lower(): match = True
            
            if match:
                for item in sec.get("items", []):
                    if item["type"] == "Тропарь" and not tropar_text:
                        if glas is None or item["glas"] == glas:
                            tropar_text, tropar_glas = item["text"], item["glas"]
                            break
        
        if tropar_text:
            key = (tropar_text[:50], tropar_glas)
            if key not in seen_tropars:
                seen_tropars.add(key)
                pairs.append({
                    "section": f"Тропарь ({ref.capitalize() if ref != 'триодь' else 'Триоди'})",
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
            match = False
            if ref == "воскресный" and "воскресн" in sec_name.lower(): match = True
            elif ref == "триодь" and ("триод" in sec_name.lower() or "пост" in sec_name.lower() or "крест" in sec_name.lower()): match = True
            elif ref == "богородица" and ("богородиц" in sec_name.lower() or "икон" in sec_name.lower()): match = True
            elif ref == "григорий" and "григор" in sec_name.lower(): match = True
            
            if match:
                for item in sec.get("items", []):
                    if item["type"] == "Кондак" and not kontak_text:
                        if glas is None or item["glas"] == glas:
                            kontak_text, kontak_glas = item["text"], item["glas"]
                            break
        
        if kontak_text:
            key = (kontak_text[:50], kontak_glas)
            if key not in seen_kontaks:
                seen_kontaks.add(key)
                pairs.append({
                    "section": f"Кондак ({ref.capitalize() if ref != 'триодь' else 'Триоди'}, глас {kontak_glas})",
                    "tropar": None,
                    "kontak": {"text": kontak_text, "glas": kontak_glas},
                    "tropar_glas": "",
                    "kontak_glas": kontak_glas,
                })

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

    ukazaniya_text = "\n\n".join([info.get("raw_text", "") for info in hours_info]) if hours_info else "Все тропари и кондаки дня"
    return ukazaniya_text, pairs

def get_pdf_sections(selected_pairs: List[Dict]) -> List[Dict]:
    """Преобразует выбранные пары в секции для генератора PDF."""
    sections = []
    for pair in selected_pairs:
        items = []
        if pair.get("tropar"):
            items.append({"type": "Тропарь", "glas": pair["tropar_glas"], "text": pair["tropar"]["text"]})
        if pair.get("kontak"):
            items.append({"type": "Кондак", "glas": pair["kontak_glas"], "text": pair["kontak"]["text"]})
        if items:
            sections.append({"section": pair["section"], "items": items})
    return sections
