"""
Günün menüsünden görsel bir kart (HTML) üretir ve JPEG'e çevirir.
"""
from __future__ import annotations

import html
import re
from datetime import date

from menu import DayMenu, MenuItem, day_name, human_date, tr_lower

# TUSAŞ kurumsal renk paleti
WHITE = "#FFFFFF"         # Pantone White
RED = "#DD140E"           # Pantone 485 C
BLUE = "#1E3685"          # Pantone Reflex Blue (RGB 30/54/133)
GRAY7 = "#97999B"         # Pantone Cool Gray 7 C
GRAY11 = "#4D4D4D"        # Pantone Cool Gray 11
BLACK = "#2D2926"         # Pantone Black
# Eski koyu tema tonları
NAVY = "#0b1b3d"
NAVY_DEEP = "#07122a"
RED_DARK = "#6e0f16"

THEMES = {
    # Açık zemin; kurumsal mavi ve kırmızı vurgu, yazılar kurumsal siyah/gri
    "acik": {
        "page": "#EFEFF0", "card": WHITE, "card_border": "#DCDDDE", "shadow": "rgba(45,41,38,.10)",
        "hero_bg": WHITE, "hero_loc": GRAY7, "hero_date": BLUE, "hero_day": GRAY11,
        "panel": WHITE, "panel_border": "#E3E4E5", "h2": BLUE, "text": BLACK,
        "line": "rgba(151,153,155,.35)", "veg_bg": "#FDECEB", "veg_border": "#F4B4B1",
        "kcal_bg": WHITE, "kcal_border": "#CFD0D1", "kcal_text": GRAY11,
        "wheat": "#ECECED", "milk": "#DDE2F0", "muted": GRAY11, "legend": GRAY11,
        "accent_main": RED, "accent_other": BLUE, "tag": RED, "stripe": RED,
    },
    # Koyu lacivert zemin
    "koyu": {
        "page": NAVY_DEEP, "card": NAVY, "card_border": "#22396b", "shadow": "rgba(0,0,0,.45)",
        "hero_bg": f"linear-gradient(120deg, #0a1a40 0%, #102a63 58%, {RED_DARK} 100%)",
        "hero_loc": "#9aa8c4", "hero_date": "#ffffff", "hero_day": "#c9d3e6",
        "panel": "#11264f", "panel_border": "#22396b", "h2": "#ffffff", "text": "#e8ecf4",
        "line": "rgba(255,255,255,.09)", "veg_bg": "rgba(163,22,31,.16)", "veg_border": "rgba(163,22,31,.55)",
        "kcal_bg": NAVY, "kcal_border": "#22396b", "kcal_text": "#c9d3e6",
        "wheat": "#4a3f12", "milk": "#1d3f6e", "muted": "#9aa8c4", "legend": "#c9d3e6",
        "accent_main": "#a3161f", "accent_other": "#3d5fa8", "tag": "#a3161f", "stripe": "#a3161f",
    },
}
DEFAULT_THEME = "acik"


def _e(s: str) -> str:
    return html.escape(s, quote=True)


def format_notes(raw_notes: list[str]) -> list[str]:
    """Alerjen açıklamalarını at, bant bilgilerini kısalt:
    "160 NOLU YEMEKHANE'DE, ... 1.4.5.8.9. BANTTA (ANKA) BULUNMAKTADIR." -> "▸ 160 nolu yemekhane → 1.4.5.8.9. bant (ANKA)"
    """
    out = []
    pat = re.compile(r"(\d+)\s*NOLU YEMEKHANE.*?MEN[ÜU]\s+(.+?)\s*BANTTA\s*(\(.*?\))?", re.I)
    for n in raw_notes:
        s = re.sub(r"\s+", " ", n).strip()
        if not s or s.startswith(("*", "+")):
            continue
        m = pat.search(s)
        if m:
            extra = f" {m.group(3)}" if m.group(3) else ""
            out.append(f"▸ {m.group(1)} nolu yemekhane → {tr_lower(m.group(2).strip())} bant{extra}")
        else:
            out.append("▸ " + s[0] + tr_lower(s[1:]))
    if any("nolu yemekhane" in o for o in out):
        out.insert(0, "Buğday unsuz çorba ve vejetaryen menü bantları:")
    return out


def _badges(it: MenuItem) -> str:
    b = ""
    if it.wheat:
        b += '<span class="al w" title="Buğday unu">🌾</span>'
    if it.milk:
        b += '<span class="al m" title="Süt ürünü">🥛</span>'
    return b


def _row(it: MenuItem, veg: bool = False) -> str:
    kcal = f'<span class="kcal">{it.kcal} kcal</span>' if it.kcal else ""
    if veg:
        name = re.sub(r"^Vej\.\s*", "", it.name)
        return (f'<li class="veg"><span class="nm"><span class="vtag">Vej.</span>{_e(name)}</span>'
                f'<span class="meta">{_badges(it)}{kcal}</span></li>')
    return f'<li><span class="nm">{_e(it.name)}</span><span class="meta">{_badges(it)}{kcal}</span></li>'


def _norm(name: str) -> str:
    return re.sub(r"[^a-zçğıöşü0-9]", "", tr_lower(name))


def split_menu(menu: DayMenu):
    """(günün menüsü, {index: [vejetaryen alternatifler]}, diğer bölümler).
    Vejetaryen menüdeki, günün menüsünde olmayan yemekler (ör. ana yemek) alternatif olarak döner;
    aynı sıradaki yemeğin hemen altına yerleştirilir."""
    main = next((x for x in menu.sections if tr_lower(x.title) == "günün menüsü"), None)
    veg = next((x for x in menu.sections if tr_lower(x.title).startswith("vejetaryen")), None)
    if not main or not veg:
        return main, {}, [x for x in menu.sections if x is not main]
    main_names = {_norm(i.name) for i in main.items}
    alts: dict[int, list[MenuItem]] = {}
    for idx, it in enumerate(veg.items):
        if _norm(it.name) not in main_names:
            alts.setdefault(min(idx, len(main.items) - 1), []).append(it)
    others = [x for x in menu.sections if x is not main and x is not veg]
    return main, alts, others


def _relative_tag(d: date, today: date) -> str:
    diff = (d - today).days
    return {0: "BUGÜN", 1: "YARIN", -1: "DÜN"}.get(diff, "")


def build_html(menu: DayMenu, title: str, today: date | None = None, theme: str = DEFAULT_THEME) -> str:
    T = THEMES.get(theme, THEMES[DEFAULT_THEME])
    today = today or date.today()
    d = menu.date
    rel = _relative_tag(d, today)
    location = title.replace("Yemek Menüsü", "").strip() or "Yemekhane"

    main, alts, others = split_menu(menu)
    sections_html = []
    for sec in ([main] if main else []) + others:
        key = tr_lower(sec.title)
        accent = T["accent_main"] if key == "günün menüsü" else T["accent_other"]
        total = sum(i.kcal for i in sec.items if i.kcal)
        total_html = f'<span class="total">≈ {total:,} kcal</span>'.replace(",", ".") if total and key != "salata büfesi" else ""
        rows = []
        for idx, it in enumerate(sec.items):
            rows.append(_row(it))
            if sec is main:
                rows += [_row(v, veg=True) for v in alts.get(idx, [])]
        sections_html.append(
            f'<section style="--c:{accent}">'
            f'<h2>{_e(sec.title)}{total_html}</h2>'
            f'<ul>{"".join(rows)}</ul></section>'
        )

    notes_html = "".join(f"<li>{_e(n)}</li>" for n in format_notes(menu.notes))
    tag_html = f'<span class="tag">{rel}</span>' if rel else ""

    return f"""<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: Consolas, "DejaVu Sans Mono", "Courier New", monospace;
    background: {T["card"]}; width: 720px; padding: 0; color: {T["text"]};
  }}
  .card {{ background: {T["card"]}; overflow: hidden; }}
  .hero {{
    position: relative; padding: 26px 30px 22px;
    background: {T["hero_bg"]};
    border-bottom: 4px solid {T["stripe"]};
  }}
  .hero .loc {{ font-size: 15px; letter-spacing: 3px; text-transform: uppercase; color: {T["hero_loc"]}; font-weight: 700; }}
  .hero .date {{ font-size: 40px; font-weight: 700; margin-top: 8px; line-height: 1.1; color: {T["hero_date"]}; }}
  .hero .day {{ font-size: 21px; margin-top: 4px; color: {T["hero_day"]}; }}
  .tag {{ display: inline-block; background: {T["tag"]}; color: #fff; font-weight: 700; font-size: 13px;
          padding: 4px 12px; border-radius: 6px; letter-spacing: 2px; margin-left: 12px; vertical-align: 7px; }}
  .body {{ padding: 20px 22px 6px; }}
  section {{ background: {T["panel"]}; border: 1px solid {T["panel_border"]}; border-left: 6px solid var(--c);
             border-radius: 14px; padding: 14px 16px 8px; margin-bottom: 14px; }}
  h2 {{ font-size: 18px; color: {T["h2"]}; display: flex; align-items: center; margin-bottom: 6px;
        letter-spacing: 1px; text-transform: uppercase; }}
  h2 .total {{ margin-left: auto; font-size: 13px; font-weight: 700; color: #fff; background: {T["tag"]};
               padding: 3px 10px; border-radius: 6px; letter-spacing: 0; text-transform: none; }}
  ul {{ list-style: none; }}
  li {{ display: flex; align-items: center; padding: 8px 4px; border-bottom: 1px dashed {T["line"]};
        font-size: 18px; color: {T["text"]}; }}
  li:last-child {{ border-bottom: 0; }}
  li.veg {{ background: {T["veg_bg"]}; border: 1px solid {T["veg_border"]}; border-radius: 10px;
            padding: 7px 10px; margin: 4px 0 6px; font-size: 17px; }}
  .vtag {{ display: inline-block; font-size: 12px; font-weight: 700; color: #fff; background: {T["tag"]};
           border-radius: 5px; padding: 2px 8px; margin-right: 10px; vertical-align: 2px; letter-spacing: 1px; }}
  .nm {{ flex: 1; font-weight: 700; }}
  .meta {{ display: flex; align-items: center; gap: 6px; }}
  .al {{ font-size: 14px; border-radius: 6px; padding: 2px 5px;
         font-family: "Segoe UI Emoji", "Noto Color Emoji", "Apple Color Emoji", sans-serif; }}
  .al.w {{ background: {T["wheat"]}; }}
  .al.m {{ background: {T["milk"]}; }}
  .kcal {{ font-size: 13px; color: {T["kcal_text"]}; background: {T["kcal_bg"]}; border: 1px solid {T["kcal_border"]};
           border-radius: 6px; padding: 2px 8px; white-space: nowrap; }}
  .foot {{ padding: 4px 26px 20px; color: {T["muted"]}; font-size: 13px; }}
  .legend {{ display: flex; gap: 22px; margin-bottom: 10px; color: {T["legend"]}; }}
  .foot ul li {{ font-size: 12.5px; color: {T["muted"]}; border: 0; padding: 2px 0; display: block; }}
</style></head>
<body><div class="card">
  <div class="hero">
    <div class="loc">{_e(location)} · Yemek Menüsü</div>
    <div class="date">{d.day} {_e(human_date(d).split(' ')[1])}{tag_html}</div>
    <div class="day">{_e(day_name(d))} · {d.year}</div>
  </div>
  <div class="body">{''.join(sections_html)}</div>
  <div class="foot">
    <div class="legend"><span><span class="al w">🌾</span> Buğday unu içerir</span><span><span class="al m">🥛</span> Süt ürünü içerir</span></div>
    <ul>{notes_html}</ul>
  </div>
</div></body></html>"""


def render_png(page_html: str, cfg: dict) -> bytes:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(channel=cfg.get("browser_channel", "msedge"), headless=True)
        try:
            page = browser.new_page(viewport={"width": 720, "height": 400}, device_scale_factor=2)
            page.set_content(page_html, wait_until="load")
            return page.locator("body").screenshot(type="jpeg", quality=90)
        finally:
            browser.close()


def build_text(menu: DayMenu, title: str) -> str:
    """Görsel üretilemezse gönderilecek HTML biçimli metin (Telegram parse_mode=HTML)."""
    d = menu.date
    lines = [f"<b>{_e(title)}</b>", f"<b>{human_date(d)} · {day_name(d)}</b>", ""]
    def line(it, prefix=""):
        kcal = f" <i>({it.kcal} kcal)</i>" if it.kcal else ""
        al = (" 🌾" if it.wheat else "") + (" 🥛" if it.milk else "")
        return f"{prefix}{_e(it.name)}{kcal}{al}"

    main, alts, others = split_menu(menu)
    for sec in ([main] if main else []) + others:
        lines.append(f"<b>{_e(sec.title)}</b>")
        for idx, it in enumerate(sec.items):
            lines.append("• " + line(it))
            if sec is main:
                lines += [line(v, "   <i>Vej.:</i> ") for v in alts.get(idx, [])]
        lines.append("")
    return "\n".join(lines).strip()
