#!/usr/bin/env python3
"""
Генератор PDF с Тропарями и Кондаками.

Библиотечный модуль — экспортирует generate_pdf_bytes(date, font_path) → bytes.
"""

import io
import os
import re
import copy
import logging
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ═══════════════════════════ Настройки вёрстки ═══════════════════════════

FONT_NAME = "Ponomar"

SHEET_WIDTH = 841.89
SHEET_HEIGHT = 595.28

FRAME_OUTER_MARGIN = 22
FRAME_INNER_OFFSET = 2.0
FRAME_LINE_WIDTH = 0.24
FRAME_GAP = 10

TEXT_PADDING_X = 20
TEXT_PADDING_TOP = 28
TEXT_PADDING_BOTTOM = 20

LINE_SPACING_K = 1.5
GAP_SECTION = 8
GAP_ITEM = 4

FONT_MIN, FONT_MAX, FONT_STEP = 10.0, 20.0, 0.1
MAX_PAGES = 4

COLOR_RED = (0.933, 0.133, 0.047)
COLOR_BLACK = (0, 0, 0)

_font_registered = False


def _zone_dims():
    half = SHEET_WIDTH / 2
    fw = half - 2 * FRAME_OUTER_MARGIN - FRAME_GAP / 2 - 2 * FRAME_INNER_OFFSET
    fh = SHEET_HEIGHT - 2 * FRAME_OUTER_MARGIN + 4 - 2 * FRAME_INNER_OFFSET
    return fw - 2 * TEXT_PADDING_X, fh - TEXT_PADDING_TOP - TEXT_PADDING_BOTTOM

ZONE_W, ZONE_H = _zone_dims()


# ═══════════════════════════ Парсинг azbyka.ru ═══════════════════════════

def fetch_page(url: str) -> str:
    logger.info(f"GET {url}")
    r = requests.get(url, timeout=30, headers={
        "User-Agent": "TropariaBot/1.0 (Orthodox liturgical helper)"
    })
    r.raise_for_status()
    return r.text


def fetch_troparia_kontakia(date: str) -> list[dict]:
    """Загружает и парсит тропари/кондаки с azbyka.ru/days/{date}."""
    html = fetch_page(f"https://azbyka.ru/days/{date}")
    return _parse_troparia(html)


def parse_hours_from_ukazaniya(html: str) -> list[dict]:
    """
    Парсит богослужебные указания и извлекает информацию о чтении на Часах.
    Ищет фрагменты с упоминанием часов (1-й, 3-й, 6-й, 9-й, "На часах").
    
    Returns:
        list[dict]: Каждый элемент содержит:
            - section: название раздела/памяти
            - raw_text: исходный текст указания
            - tropar_ref: ссылка на тропарь (если указана)
            - kontak_ref: ссылка на кондак (если указана)
    """
    soup = BeautifulSoup(html, "html.parser")
    hours_readings = []
    
    # Паттерны для поиска упоминаний часов
    hours_patterns = [
        r'на\s+\d+-?[м]?м?\s+часа?х?',  # "на 1-м часах", "на 3-м часе", "на 6-м часах"
        r'на\s+часа?х?',  # "на часах"
        r'1-?[й]?м?\s+час',  # "1-й час"
        r'3-?[й]?м?\s+час',  # "3-й час"
        r'6-?[й]?м?\s+час',  # "6-й час"
        r'9-?[й]?м?\s+час',  # "9-й час"
    ]
    
    # Ищем все параграфы, содержащие упоминания часов
    for p in soup.find_all("p"):
        p_text = p.get_text(strip=True)
        p_lower = p_text.lower()
        
        # Проверяем, есть ли упоминание часов
        has_hours = any(re.search(pattern, p_lower) for pattern in hours_patterns)
        
        if has_hours and ('тропар' in p_lower or 'кондак' in p_lower):
            # Нашли указание о чтении на часах
            raw_text = p_text
            
            # Пытаемся определить, какой памяти/разделу принадлежит
            section_name = "Общее"
            
            # Ищем предыдущий h3 или заголовок
            prev = p.find_previous_sibling()
            search_depth = 0
            while prev and search_depth < 15:
                if prev.name in ("h3", "h4", "h5"):
                    section_name = prev.get_text(strip=True)
                    break
                prev = prev.find_previous_sibling()
                search_depth += 1
            
            # Если не нашли, ищем в предыдущих параграфах ключевые слова
            if section_name == "Общее":
                prev_p = p.find_previous_sibling("p")
                if prev_p:
                    prev_text = prev_p.get_text(strip=True)
                    # Ищем имена святых или праздников
                    if "блж" in prev_text.lower() or "матрон" in prev_text.lower():
                        section_name = "Блаженной Матроне Московской"
                    elif "григор" in prev_text.lower() or "палам" in prev_text.lower():
                        section_name = "Святителю Григорию Паламе"
                    elif "трио" in prev_text.lower() or "пост" in prev_text.lower():
                        section_name = "Триодь (Пост)"
                    elif "воскрес" in prev_text.lower():
                        section_name = "Воскресные"
                    elif "поликарп" in prev_text.lower():
                        section_name = "Священномученику Поликарпу"
                    elif "заупокой" in prev_text.lower() or "упокой" in prev_text.lower():
                        section_name = "Заупокойная"
            
            hours_readings.append({
                "section": section_name,
                "raw_text": raw_text,
                "tropar_ref": "tropar" if "тропар" in p_lower else None,
                "kontak_ref": "kontak" if "кондак" in p_lower else None,
            })
    
    return hours_readings


def _parse_troparia(html: str) -> list[dict]:
    """
    Парсит тропари/кондаки из HTML azbyka.ru.
    Поддерживает новую структуру с <h2 class="block_title"> и <h3>.
    """
    soup = BeautifulSoup(html, "html.parser")
    sections = []
    
    # Находим все заголовки секций h2.block_title
    h2_elements = list(soup.find_all("h2", class_="block_title"))
    
    # Если нет секций с block_title, используем старый метод
    if not h2_elements:
        return _parse_troparia_old(html)
    
    # Для каждой секции ищем тропари/кондаки
    for idx, h2_elem in enumerate(h2_elements):
        section_name = h2_elem.get_text(strip=True)
        if not section_name:
            continue
        
        # Определяем границу: следующий h2.block_title или конец документа
        items = []
        current = h2_elem.next_sibling
        
        while current:
            # Пропускаем текстовые узлы (NavigableString)
            if not hasattr(current, 'name') or current.name is None:
                current = current.next_sibling
                continue
                
            # Если достигли следующего h2.block_title - останавливаемся
            if current.name == "h2" and "block_title" in current.get("class", []):
                break
                
            # Если это div.frame - обрабатываем
            if current.name == "div" and "frame" in current.get("class", []):
                h3 = current.find("h3")
                if h3:
                    h3_text = h3.get_text(strip=True)
                    
                    # Проверяем, содержит ли заголовок "Тропарь" или "Кондак"
                    match = re.search(r'(Тропарь|Кондак|Ин Тропарь|Ин Кондак).*?глас\s*(\d+)', h3_text, re.IGNORECASE)
                    if match:
                        item_type_full = match.group(1)
                        # Нормализуем тип
                        if "Ин Тропарь" in item_type_full:
                            item_type = "Тропарь"
                        elif "Ин Кондак" in item_type_full:
                            item_type = "Кондак"
                        else:
                            item_type = item_type_full
                            
                        glas = match.group(2)
                        
                        # Ищем текст в div внутри frame
                        # Структура: div.frame > div.inner.taks_content > div > p > p(текст)
                        text_div = current.find("div", class_="taks_content")
                        if text_div:
                            # Находим первый div внутри taks_content
                            inner_div = text_div.find("div", recursive=False)
                            if inner_div:
                                # Находим p внутри inner_div
                                p_elem = inner_div.find("p", recursive=False)
                                if p_elem:
                                    # Текст может быть в этом же p или во вложенном
                                    inner_p = p_elem.find("p", recursive=False)
                                    if inner_p:
                                        text = inner_p.get_text(strip=True)
                                    else:
                                        text = p_elem.get_text(strip=True)
                                    
                                    if text and not text.startswith("Перевод:"):
                                        items.append({"type": item_type, "glas": glas, "text": text})
            
            current = current.next_sibling
        
        if items:
            sections.append({"section": section_name, "items": items})
    
    return sections


def _parse_troparia_old(html: str) -> list[dict]:
    """
    Старый метод парсинга для обратной совместимости.
    """
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    sections, cur = [], None
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        m = re.match(r'^(Тропарь|Кондак),\s*глас\s*(\d+)', line)
        if m:
            it, gl = m.group(1), m.group(2)
            tl = []
            i += 1
            while i < len(lines):
                nl = lines[i].strip()
                if not nl:
                    i += 1; continue
                if re.match(r'^(Тропарь|Кондак),\s*глас', nl):
                    break
                if re.match(
                    r'^(Воскресн|В Неделю|Святител|Преподобн|Мученик|Праздник|Богородиц)',
                    nl
                ) and not tl:
                    cur = nl; i += 1; break
                tl.append(nl)
                i += 1
            t = " ".join(tl)
            if cur is None:
                cur = "Общие"
            for s in sections:
                if s["section"] == cur:
                    s["items"].append({"type": it, "glas": gl, "text": t})
                    break
            else:
                sections.append({
                    "section": cur,
                    "items": [{"type": it, "glas": gl, "text": t}]
                })
            continue
        if re.match(
            r'^(Воскресн|В Неделю|Святител|Преподобн|Мученик|Праздник|Богородиц)',
            line
        ) and len(line) < 200:
            cur = line
            i += 1
            continue
        i += 1
    return sections


# ═══════════════════════════ OTF → TTF ═══════════════════════════

def _convert_otf_to_ttf(otf_path: str) -> str:
    """Конвертирует OTF (CFF) в TTF для reportlab. Кеширует результат."""
    import tempfile
    base = os.path.basename(otf_path).rsplit(".", 1)[0] + ".ttf"
    ttf_path = os.path.join(tempfile.gettempdir(), base)

    if os.path.exists(ttf_path):
        return ttf_path

    try:
        from fontTools.ttLib import TTFont as FT
        from fontTools.pens.cu2quPen import Cu2QuPen
        from fontTools.pens.ttGlyphPen import TTGlyphPen
        from fontTools.pens.recordingPen import RecordingPen
        from fontTools.ttLib.tables._g_l_y_f import table__g_l_y_f
        from fontTools.ttLib.tables._l_o_c_a import table__l_o_c_a

        logger.info("Конвертирую OTF → TTF...")
        otf = FT(otf_path)
        if 'CFF ' not in otf:
            otf.close()
            return otf_path

        go = otf.getGlyphOrder()
        cs = otf['CFF '].cff.topDictIndex[0].CharStrings
        gl = {}
        for gn in go:
            r = RecordingPen()
            cs[gn].draw(r)
            tp = TTGlyphPen(None)
            r.replay(Cu2QuPen(tp, max_err=1.0, reverse_direction=True))
            try:
                gl[gn] = tp.glyph()
            except Exception:
                gl[gn] = TTGlyphPen(None).glyph()

        t = FT()
        t.setGlyphOrder(go)
        for tag in ['cmap', 'head', 'hhea', 'hmtx', 'maxp', 'name',
                     'OS/2', 'post', 'GDEF', 'GPOS', 'GSUB']:
            if tag in otf:
                t[tag] = copy.deepcopy(otf[tag])

        t['loca'] = table__l_o_c_a()
        t['glyf'] = table__g_l_y_f()
        t['glyf'].glyphs = gl
        t['glyf'].glyphOrder = go
        t['head'].glyphDataFormat = 0
        t['head'].flags |= (1 << 3)
        t['maxp'].tableVersion = 0x00010000
        t['maxp'].numGlyphs = len(go)
        for a in ['maxZones', 'maxTwilightPoints', 'maxStorage', 'maxFunctionDefs',
                   'maxInstructionDefs', 'maxStackElements', 'maxSizeOfInstructions',
                   'maxComponentElements', 'maxComponentDepth', 'maxCompositePoints',
                   'maxCompositeContours']:
            setattr(t['maxp'], a, 0)
        t['maxp'].maxZones = 2
        t['maxp'].maxPoints = 200
        t['maxp'].maxContours = 50
        t.save(ttf_path)
        otf.close()
        logger.info(f"TTF сохранён: {ttf_path}")
        return ttf_path
    except ImportError:
        logger.warning("fonttools не установлен, пробую OTF напрямую")
        return otf_path


def _ensure_font(font_path: str):
    """Регистрирует шрифт в reportlab (один раз)."""
    global _font_registered
    if _font_registered:
        return

    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont as RLFont

    fp = font_path
    if fp.endswith('.otf'):
        fp = _convert_otf_to_ttf(fp)

    pdfmetrics.registerFont(RLFont(FONT_NAME, fp))
    _font_registered = True
    logger.info("Шрифт зарегистрирован")


# ═══════════════════════════ Измерения и блоки ═══════════════════════════

def _nlines(text: str, fs: float) -> int:
    from reportlab.pdfbase import pdfmetrics
    words = text.split()
    n, cur = 0, ""
    for w in words:
        t = f"{cur} {w}".strip() if cur else w
        if pdfmetrics.stringWidth(t, FONT_NAME, fs) > ZONE_W and cur:
            n += 1; cur = w
        else:
            cur = t
    return n + (1 if cur else 0)


def _heading_h(heading, fs):
    return _nlines(heading, fs) * fs * LINE_SPACING_K + GAP_SECTION

def _item_h(item, fs):
    lh = fs * LINE_SPACING_K
    return lh + _nlines(item.get("text", ""), fs) * lh + GAP_ITEM

def _section_h(sec, fs):
    h = _heading_h(sec["section"], fs) if sec.get("section") else 0
    for item in sec.get("items", []):
        h += _item_h(item, fs)
    return h


def _build_blocks(sections, fs):
    """
    Строит один большой блок со всеми секциями и элементами подряд.
    Рендерер будет размещать элементы непрерывно, переходя между колонками.
    """
    all_parts = []
    total_height = 0.0
    
    for sec in sections:
        heading = sec.get("section", "")
        items = sec.get("items", [])
        
        # Добавляем заголовок секции
        if heading:
            all_parts.append({"kind": "heading", "text": heading,
                              "lines": _nlines(heading, fs)})
            total_height += _heading_h(heading, fs)
        
        # Добавляем все items из этой секции
        for item in items:
            all_parts.append({
                "kind": "item",
                "label": f"{item['type']}, глас {item['glas']}",
                "text": item.get("text", ""),
                "text_lines": _nlines(item.get("text", ""), fs),
            })
            total_height += _item_h(item, fs)
    
    # Возвращаем один большой блок со всеми элементами
    return [{"height": total_height, "parts": all_parts}] if all_parts else []


def _find_optimal_font(sections, max_pages=MAX_PAGES):
    """
    Подбирает шрифт так, чтобы:
    1. Заполнить минимум 2 страницы (если контента достаточно)
    2. На странице помещалось примерно 2 текста (тропарь+кондак = ~1 пара)
    """
    # Считаем общее количество элементов
    total_items = sum(len(sec.get("items", [])) for sec in sections)
    
    # Целевое количество страниц: минимум 2, но не больше max_pages
    # Примерно 1-2 пары на страницу
    target_pages = max(2, min(max_pages, total_items // 2))
    if target_pages < 2 and total_items > 0:
        target_pages = 2
    
    # Начинаем с максимального шрифта и уменьшаем, пока не заполним нужное количество страниц
    fs = FONT_MAX
    best_fs = 0.0
    
    while fs >= FONT_MIN:
        blocks = _build_blocks(sections, fs)
        if not blocks:
            fs = round(fs - FONT_STEP, 1)
            continue
            
        # Считаем, сколько страниц займёт контент
        total_height = sum(b["height"] for b in blocks)
        pages_needed = max(1, int((total_height - 1) / ZONE_H) + 1)
        
        # Запоминаем последний шрифт, который укладывается в max_pages
        if pages_needed <= max_pages:
            best_fs = fs
        
        # Если заполняем хотя бы target_pages — это хороший вариант
        if pages_needed >= target_pages and pages_needed <= max_pages:
            return fs
        
        fs = round(fs - FONT_STEP, 1)
    
    # Если не нашли оптимальный, возвращаем лучший из подходящих под max_pages
    return best_fs if best_fs > FONT_MIN else FONT_MIN


# ═══════════════════════════ PDF-рендер ═══════════════════════════

class _TwoUpRenderer:
    def __init__(self, c, fs):
        self.c = c; self.fs = fs; self.lh = fs * LINE_SPACING_K
        self.half = SHEET_WIDTH / 2
        self.frames = {"L": self._fr(0), "R": self._fr(self.half)}
        self.zones = {k: self._zn(f) for k, f in self.frames.items()}
        self.side = "L"; self.y = 0; self._sheet()

    def _fr(self, xo):
        ow = self.half - 2 * FRAME_OUTER_MARGIN - FRAME_GAP / 2
        oh = SHEET_HEIGHT - 2 * FRAME_OUTER_MARGIN + 4
        ox, oy = xo + FRAME_OUTER_MARGIN, FRAME_OUTER_MARGIN - 2
        return {
            "outer": (ox, oy, ow, oh),
            "inner": (ox + FRAME_INNER_OFFSET, oy + FRAME_INNER_OFFSET,
                      ow - 2 * FRAME_INNER_OFFSET, oh - 2 * FRAME_INNER_OFFSET),
        }

    def _zn(self, f):
        ix, iy, iw, ih = f["inner"]
        return {"x": ix + TEXT_PADDING_X, "top": iy + ih - TEXT_PADDING_TOP,
                "bot": iy + TEXT_PADDING_BOTTOM, "w": iw - 2 * TEXT_PADDING_X}

    def _borders(self):
        self.c.setStrokeColorRGB(0, 0, 0)
        self.c.setLineWidth(FRAME_LINE_WIDTH)
        for s in ("L", "R"):
            for k in ("outer", "inner"):
                x, y, w, h = self.frames[s][k]
                self.c.rect(x, y, w, h, stroke=1, fill=0)

    def _sheet(self):
        self._borders()
        self.side = "L"
        self.y = self.zones["L"]["top"]

    @property
    def z(self):
        return self.zones[self.side]

    def _flip(self):
        if self.side == "L":
            self.side = "R"
            self.y = self.zones["R"]["top"]
        else:
            self.c.showPage()
            self._sheet()

    def _ensure(self, h):
        if self.y - h < self.z["bot"]:
            self._flip()

    def _centered(self, text, color):
        z = self.z
        self.c.setFont(FONT_NAME, self.fs)
        self.c.setFillColorRGB(*color)
        tw = self.c.stringWidth(text, FONT_NAME, self.fs)
        self.c.drawString(z["x"] + max(0, (z["w"] - tw) / 2), self.y, text)
        self.y -= self.lh

    def _wrapped(self, text, color):
        """Переносит текст по строкам и рисует, проверяя место для каждой строки."""
        z = self.z
        self.c.setFont(FONT_NAME, self.fs)
        words = text.split()
        lns, cur = [], ""
        for w in words:
            t = f"{cur} {w}".strip() if cur else w
            if self.c.stringWidth(t, FONT_NAME, self.fs) > z["w"] and cur:
                lns.append(cur); cur = w
            else:
                cur = t
        if cur:
            lns.append(cur)
        
        # Рисуем каждую строку с проверкой места
        for ln in lns:
            # Проверяем, влезет ли строка
            if self.y - self.lh < z["bot"]:
                self._flip()
                z = self.z
            
            self.c.setFont(FONT_NAME, self.fs)
            self.c.setFillColorRGB(*color)
            self.c.drawString(z["x"], self.y, ln)
            self.y -= self.lh

    def render_block(self, block):
        """Рендерит блок по частям, текст идёт непрерывным потоком."""
        for part in block["parts"]:
            if part["kind"] == "heading":
                part_h = _heading_h(part["text"], self.fs)
                # Ensure heading + small buffer (e.g. 2 lines of next item)
                self._ensure(part_h + 2 * self.lh)
                
                z = self.z
                self.c.setFont(FONT_NAME, self.fs)
                tw = self.c.stringWidth(part["text"], FONT_NAME, self.fs)
                if tw > z["w"]:
                    self._wrapped(part["text"], COLOR_RED)
                else:
                    self._centered(part["text"], COLOR_RED)
                # GAP_SECTION уже учтён в _heading_h
                
            elif part["kind"] == "item":
                # Label (always centered, one line)
                # Ensure label + at least 3 lines of text (or all if shorter)
                keep_lines = min(part.get("text_lines", 0), 3)
                item_min_h = self.lh + keep_lines * self.lh + GAP_ITEM
                self._ensure(item_min_h)
                
                self._centered(part["label"], COLOR_RED)
                # self.y уже уменьшен на self.lh в _centered
                
                # Text (wrapped, flows continuously)
                if part.get("text"):
                    self._wrapped(part["text"], COLOR_BLACK)
                
                # GAP_ITEM добавляется в конце item
                self.y -= GAP_ITEM


# ═══════════════════════════ Публичный API ═══════════════════════════

def generate_pdf_bytes(date: str, font_path: str, sections: list = None) -> bytes:
    """
    Главная функция: дата → PDF в виде bytes.

    Args:
        date: Дата в формате YYYY-MM-DD
        font_path: Путь к файлу шрифта
        sections: Опционально. Список секций для генерации.
                  Если None, загружает данные с azbyka.ru

    1. Загружает данные с azbyka.ru (или использует переданные)
    2. Подбирает шрифт
    3. Генерирует PDF
    4. Возвращает bytes
    """
    from reportlab.pdfgen import canvas

    _ensure_font(font_path)

    # Загрузка данных
    if sections is None:
        sections = fetch_troparia_kontakia(date)

        if not sections:
            raise ValueError(f"Не найдены тропари/кондаки на {date}")

    logger.info(f"Найдено секций: {len(sections)}")
    for s in sections:
        logger.info(f"  {s['section']}: {len(s['items'])} текст(ов)")

    # Подбор шрифта и блоков
    fs = _find_optimal_font(sections)
    blocks = _build_blocks(sections, fs)
    logger.info(f"Шрифт: {fs:.1f}pt, блоков: {len(blocks)}")

    # Генерация PDF в буфер
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(SHEET_WIDTH, SHEET_HEIGHT))
    renderer = _TwoUpRenderer(c, fs)

    for block in blocks:
        renderer.render_block(block)

    c.save()
    pdf_bytes = buf.getvalue()
    buf.close()

    logger.info(f"PDF: {len(pdf_bytes)} байт")
    return pdf_bytes
