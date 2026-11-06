"""
Günün menüsünden emojili, görsel bir kart (HTML) üretir ve PNG'ye çevirir.
"""
from __future__ import annotations

import html
import re
from datetime import date

from menu import DayMenu, MenuItem, day_name, human_date, tr_lower

SECTION_STYLE = {
    "günün menüsü": ("🍽️", "#e8590c", "#fff4e6"),
    "vejetaryen menü": ("🌱", "#2f9e44", "#ebfbee"),
    "salata büfesi": ("🥗", "#0c8599", "#e3fafc"),
}
DEFAULT_SECTION = ("✨", "#5f3dc4", "#f3f0ff")


def _e(s: str) -> str:
    return html.escape(s, quote=True)


def format_notes(raw_notes: list[str]) -> list[str]:
    """Alerjen açıklamalarını at, bant bilgilerini kısalt:
    "160 NOLU YEMEKHANE'DE, ... 1.4.5.8.9. BANTTA (ANKA) BULUNMAKTADIR." -> "📍 160 nolu yemekhane → 1.4.5.8.9. bant (ANKA)"
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
            out.append(f"📍 {m.group(1)} nolu yemekhane → {tr_lower(m.group(2).strip())} bant{extra}")
        else:
            out.append("ℹ️ " + s[0] + tr_lower(s[1:]))
    if any(o.startswith("📍") for o in out):
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
        return (f'<li class="veg"><span class="nm"><span class="vtag">🌱 Vejetaryen</span>{_e(name)}</span>'
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


def build_html(menu: DayMenu, title: str, today: date | None = None) -> str:
    today = today or date.today()
    d = menu.date
    rel = _relative_tag(d, today)
    location = title.replace("Yemek Menüsü", "").strip() or "Yemekhane"

    main, alts, others = split_menu(menu)
    sections_html = []
    for sec in ([main] if main else []) + others:
        key = tr_lower(sec.title)
        icon, color, bg = SECTION_STYLE.get(key, DEFAULT_SECTION)
        total = sum(i.kcal for i in sec.items if i.kcal)
        total_html = f'<span class="total">≈ {total:,} kcal</span>'.replace(",", ".") if total and key != "salata büfesi" else ""
        rows = []
        for idx, it in enumerate(sec.items):
            rows.append(_row(it))
            if sec is main:
                rows += [_row(v, veg=True) for v in alts.get(idx, [])]
        sections_html.append(
            f'<section style="--c:{color};--bg:{bg}">'
            f'<h2><span class="sic">{icon}</span>{_e(sec.title)}{total_html}</h2>'
            f'<ul>{"".join(rows)}</ul></section>'
        )

    notes_html = "".join(f"<li>{_e(n)}</li>" for n in format_notes(menu.notes))

    return f"""<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: "Segoe UI", "Segoe UI Emoji", system-ui, -apple-system, "Noto Sans", Arial, sans-serif;
    background: #f1f3f9; width: 720px; padding: 22px;
  }}
  .card {{ background: #fff; border-radius: 26px; overflow: hidden;
          box-shadow: 0 10px 30px rgba(20, 30, 70, .12); }}
  .hero {{
    position: relative; color: #fff; padding: 26px 30px 24px;
    background: radial-gradient(circle at 85% 15%, rgba(255,255,255,.18) 0 70px, transparent 71px),
                linear-gradient(135deg, #1b2a6b 0%, #2d4bb5 55%, #e8590c 130%);
  }}
  .hero .loc {{ font-size: 15px; letter-spacing: 2.5px; text-transform: uppercase; opacity: .85; font-weight: 600; }}
  .hero .date {{ font-size: 40px; font-weight: 800; margin-top: 6px; line-height: 1.1; }}
  .hero .day {{ font-size: 22px; font-weight: 500; opacity: .95; margin-top: 2px; }}
  .hero .big {{ position: absolute; right: 30px; top: 22px; font-size: 64px; }}
  .tag {{ display: inline-block; background: #ffd43b; color: #1b2a6b; font-weight: 800; font-size: 13px;
          padding: 4px 12px; border-radius: 999px; letter-spacing: 1.5px; margin-left: 10px; vertical-align: 6px; }}
  .body {{ padding: 20px 22px 8px; }}
  section {{ background: var(--bg); border-radius: 18px; padding: 14px 16px 8px; margin-bottom: 14px;
             border-left: 6px solid var(--c); }}
  h2 {{ font-size: 19px; color: var(--c); display: flex; align-items: center; margin-bottom: 6px; }}
  h2 .sic {{ font-size: 22px; margin-right: 9px; }}
  h2 .total {{ margin-left: auto; font-size: 13px; font-weight: 700; color: #fff; background: var(--c);
               padding: 3px 10px; border-radius: 999px; }}
  ul {{ list-style: none; }}
  li {{ display: flex; align-items: center; padding: 8px 4px; border-bottom: 1px dashed rgba(0,0,0,.08);
        font-size: 18px; color: #212529; }}
  li:last-child {{ border-bottom: 0; }}
  li.veg {{ background: #ebfbee; border: 1px solid #b2f2bb; border-radius: 12px; padding: 7px 10px;
            margin: 4px 0 6px; font-size: 17px; }}
  .vtag {{ display: inline-block; font-size: 12px; font-weight: 700; color: #fff; background: #2f9e44;
           border-radius: 999px; padding: 2px 9px; margin-right: 9px; vertical-align: 2px; }}
  .nm {{ flex: 1; font-weight: 600; }}
  .meta {{ display: flex; align-items: center; gap: 6px; }}
  .al {{ font-size: 14px; border-radius: 8px; padding: 2px 5px; }}
  .al.w {{ background: #ffec99; }}
  .al.m {{ background: #a5d8ff; }}
  .kcal {{ font-size: 13px; color: #495057; background: #fff; border: 1px solid #dee2e6;
           border-radius: 999px; padding: 2px 9px; font-weight: 600; white-space: nowrap; }}
  .foot {{ padding: 4px 26px 20px; color: #6c757d; font-size: 13px; }}
  .legend {{ display: flex; gap: 18px; margin-bottom: 8px; font-weight: 600; color: #495057; }}
  .foot ul li {{ font-size: 12.5px; color: #6c757d; border: 0; padding: 2px 0; display: block; }}
</style></head>
<body><div class="card">
  <div class="hero">
    <div class="loc">🏭 {_e(location)} · Yemek Menüsü</div>
    <div class="date">{d.day} {_e(human_date(d).split(' ')[1])}{f'<span class="tag">{rel}</span>' if rel else ''}</div>
    <div class="day">{_e(day_name(d))} · {d.year}</div>
    <div class="big">🍽️</div>
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
    lines = [f"<b>🍽️ {_e(title)}</b>", f"📅 <b>{human_date(d)} · {day_name(d)}</b>", ""]
    def line(it, prefix=""):
        kcal = f" <i>({it.kcal} kcal)</i>" if it.kcal else ""
        al = (" 🌾" if it.wheat else "") + (" 🥛" if it.milk else "")
        return f"{prefix}{_e(it.name)}{kcal}{al}"

    main, alts, others = split_menu(menu)
    for sec in ([main] if main else []) + others:
        icon = SECTION_STYLE.get(tr_lower(sec.title), DEFAULT_SECTION)[0]
        lines.append(f"{icon} <b>{_e(sec.title)}</b>")
        for idx, it in enumerate(sec.items):
            lines.append("• " + line(it))
            if sec is main:
                lines += [line(v, "   🌱 <i>Vejetaryen:</i> ") for v in alts.get(idx, [])]
        lines.append("")
    return "\n".join(lines).strip()
