"""
TUSAŞ intranet yemek menüsü: sayfayı çekme (Playwright + kurulu Edge/Chrome) ve ayrıştırma.
"""
from __future__ import annotations

import html as html_lib
import json
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup

TR_MONTHS = {
    "ocak": 1, "şubat": 2, "mart": 3, "nisan": 4, "mayıs": 5, "haziran": 6,
    "temmuz": 7, "ağustos": 8, "eylül": 9, "ekim": 10, "kasım": 11, "aralık": 12,
}
TR_MONTH_NAMES = {v: k.capitalize() for k, v in TR_MONTHS.items()}
TR_DAYS = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]


# --------------------------------------------------------------------------- #
# Türkçe yazım yardımcıları
# --------------------------------------------------------------------------- #
def tr_lower(s: str) -> str:
    return s.replace("I", "ı").replace("İ", "i").lower()


def tr_title(s: str) -> str:
    words = []
    for w in tr_lower(s).split(" "):
        if not w:
            continue
        first = w[0]
        first = "İ" if first == "i" else ("I" if first == "ı" else first.upper())
        words.append(first + w[1:])
    return " ".join(words)


def _clean_name(raw: str) -> str:
    s = re.sub(r"\s+", " ", raw).strip()
    s = re.sub(r"\bZ\.\s*Y\.\s*", "ZEYTİNYAĞLI ", s, flags=re.I)
    s = re.sub(r"\bVEJ\.\s*", "VEJ. ", s, flags=re.I)
    s = re.sub(r"\s*/\s*", " / ", s)
    s = re.sub(r"-\s*(?=[A-ZÇĞİÖŞÜ])", "- ", s)  # "TAVUK- PATATES"
    return tr_title(s)


# --------------------------------------------------------------------------- #
# Veri modeli
# --------------------------------------------------------------------------- #
@dataclass
class MenuItem:
    name: str
    kcal: int | None = None
    wheat: bool = False   # '+' : buğday unu içerir
    milk: bool = False    # '*' : süt ve ürünlerini içerir


@dataclass
class MenuSection:
    title: str
    items: list[MenuItem] = field(default_factory=list)


@dataclass
class DayMenu:
    day: str                     # ISO tarih: 2026-11-06
    label: str                   # "6 Kasım 2026 - Cuma"
    sections: list[MenuSection] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def date(self) -> date:
        return date.fromisoformat(self.day)


def parse_item(text: str) -> MenuItem:
    t = re.sub(r"\s+", " ", text).strip()
    kcal = None
    m = re.search(r"-\s*(\d+)\s*kkal\s*$", t, flags=re.I)
    if m:
        kcal = int(m.group(1))
        t = t[: m.start()].strip()
    wheat = "+" in t
    milk = "*" in t
    t = t.replace("+", "").replace("*", "")
    return MenuItem(name=_clean_name(t), kcal=kcal, wheat=wheat, milk=milk)


def parse_tr_date(label: str) -> date | None:
    m = re.search(r"(\d{1,2})\s+([A-Za-zÇĞİÖŞÜçğıöşü]+)\s+(\d{4})", label)
    if not m:
        return None
    month = TR_MONTHS.get(tr_lower(m.group(2)))
    if not month:
        return None
    return date(int(m.group(3)), month, int(m.group(1)))


def parse_menu_html(page_html: str) -> list[DayMenu]:
    soup = BeautifulSoup(page_html, "html.parser")
    days: list[DayMenu] = []

    for block in soup.select(".yemek_list .yemek_list__item"):
        header = block.find("header")
        label = re.sub(r"\s+", " ", header.get_text(" ", strip=True)) if header else ""
        btn = block.select_one("[data-meal-date]")
        if btn and btn.get("data-meal-date"):
            label = btn["data-meal-date"].strip()
        d = parse_tr_date(label)
        if not d:
            continue

        menu = DayMenu(day=d.isoformat(), label=label)
        current: MenuSection | None = None

        ul = block.select_one(".phone-list ul") or block.select_one(".yml_content ul")
        if ul:
            for li in ul.find_all("li", recursive=False):
                h2 = li.find("h2")
                if h2:
                    current = MenuSection(title=tr_title(h2.get_text(" ", strip=True)))
                    menu.sections.append(current)
                    continue
                text = li.get_text(" ", strip=True)
                if not text:
                    continue
                if li.find(["i", "u"]):
                    menu.notes.append(text)
                    continue
                if current is None:
                    current = MenuSection(title="Günün Menüsü")
                    menu.sections.append(current)
                current.items.append(parse_item(text))

        # Yedek: liste yoksa butondaki JSON'dan al (bölüm başlıkları olmadan)
        if not any(s.items for s in menu.sections) and btn and btn.get("data-meal-items"):
            try:
                items = json.loads(html_lib.unescape(btn["data-meal-items"]))
                menu.sections = [MenuSection("Günün Menüsü", [parse_item(i["name"]) for i in items])]
            except (ValueError, KeyError):
                pass

        if any(s.items for s in menu.sections):
            days.append(menu)

    days.sort(key=lambda m: m.day)
    return days


def page_title(page_html: str) -> str:
    soup = BeautifulSoup(page_html, "html.parser")
    h = soup.select_one("header.main_header h2")
    return re.sub(r"\s+", " ", h.get_text(" ", strip=True)) if h else "Yemek Menüsü"


# --------------------------------------------------------------------------- #
# Sayfayı çekme
# --------------------------------------------------------------------------- #
class LoginRequired(Exception):
    """Portal oturumu yok / düşmüş."""


def _launch_persistent(p, cfg: dict, headless: bool):
    channel = cfg.get("browser_channel", "msedge")
    profile = Path(cfg.get("browser_profile_dir", "browser_profile")).resolve()
    profile.mkdir(parents=True, exist_ok=True)
    return p.chromium.launch_persistent_context(
        str(profile),
        channel=channel,
        headless=headless,
        ignore_https_errors=bool(cfg.get("ignore_https_errors", False)),
        locale="tr-TR",
        args=["--auth-server-allowlist=*.tai.com.tr", "--auth-negotiate-delegate-allowlist=*.tai.com.tr"],
    )


def _restore_cookies(ctx, cookie_file: Path):
    if cookie_file.exists():
        try:
            ctx.add_cookies(json.loads(cookie_file.read_text(encoding="utf-8")))
        except Exception:
            pass


def _save_cookies(ctx, cookie_file: Path):
    try:
        cookie_file.write_text(json.dumps(ctx.cookies(), ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def fetch_page_html(cfg: dict) -> str:
    from playwright.sync_api import sync_playwright

    url = cfg["menu_url"]
    cookie_file = Path(cfg.get("cookie_file", "portal_cookies.json"))
    with sync_playwright() as p:
        ctx = _launch_persistent(p, cfg, headless=True)
        try:
            _restore_cookies(ctx, cookie_file)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            try:
                page.wait_for_selector(".yemek_list__item", timeout=20_000)
            except Exception:
                raise LoginRequired(f"Menü bulunamadı (açılan adres: {page.url})")
            html = page.content()
            _save_cookies(ctx, cookie_file)
            return html
        finally:
            ctx.close()


def interactive_login(cfg: dict, wait_seconds: int = 300) -> bool:
    """Görünür tarayıcı açar; kullanıcı portala giriş yapınca oturumu kaydeder."""
    from playwright.sync_api import sync_playwright

    cookie_file = Path(cfg.get("cookie_file", "portal_cookies.json"))
    with sync_playwright() as p:
        ctx = _launch_persistent(p, cfg, headless=False)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(cfg["menu_url"], wait_until="domcontentloaded", timeout=60_000)
            print("Açılan tarayıcıda portala giriş yap. Menü sayfası görününce otomatik kaydedilecek...")
            deadline = time.time() + wait_seconds
            while time.time() < deadline:
                try:
                    if page.url.startswith(cfg["menu_url"].split("#")[0]) and page.query_selector(".yemek_list__item"):
                        _save_cookies(ctx, cookie_file)
                        print("✅ Oturum kaydedildi.")
                        return True
                    if "/meal/" not in page.url and "login" not in page.url.lower() and page.query_selector(".site_main"):
                        page.goto(cfg["menu_url"], wait_until="domcontentloaded")
                except Exception:
                    pass
                time.sleep(2)
            print("⏱️ Süre doldu, giriş algılanamadı.")
            return False
        finally:
            ctx.close()


# --------------------------------------------------------------------------- #
# Önbellek
# --------------------------------------------------------------------------- #
class MenuStore:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.cache_file = Path(cfg.get("cache_file", "menu_cache.json"))
        self.ttl = int(cfg.get("cache_minutes", 120)) * 60
        self.title = "Yemek Menüsü"
        self.days: dict[str, DayMenu] = {}
        self.fetched_at = 0.0
        self._load()

    def _load(self):
        if not self.cache_file.exists():
            return
        try:
            data = json.loads(self.cache_file.read_text(encoding="utf-8"))
            self.title = data.get("title", self.title)
            self.fetched_at = data.get("fetched_at", 0)
            for d in data["days"]:
                secs = [MenuSection(s["title"], [MenuItem(**i) for i in s["items"]]) for s in d["sections"]]
                self.days[d["day"]] = DayMenu(d["day"], d["label"], secs, d.get("notes", []))
        except Exception:
            self.days = {}

    def _save(self):
        data = {"title": self.title, "fetched_at": self.fetched_at,
                "days": [asdict(d) for d in self.days.values()]}
        self.cache_file.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    def refresh(self, force: bool = False):
        if not force and self.days and time.time() - self.fetched_at < self.ttl:
            return
        html = fetch_page_html(self.cfg)
        self.load_html(html)

    def load_html(self, html: str):
        parsed = parse_menu_html(html)
        if not parsed:
            raise LoginRequired("Sayfa açıldı ama menü ayrıştırılamadı.")
        self.title = page_title(html)
        # Eski günleri koru (önceki/sonraki gezinti için), yenileri üzerine yaz
        for d in parsed:
            self.days[d.day] = d
        cutoff = date.today().toordinal() - 21
        self.days = {k: v for k, v in sorted(self.days.items()) if v.date.toordinal() >= cutoff}
        self.fetched_at = time.time()
        self._save()

    def get(self, d: date) -> DayMenu | None:
        return self.days.get(d.isoformat())

    def neighbors(self, d: date) -> tuple[date | None, date | None]:
        keys = sorted(self.days)
        iso = d.isoformat()
        prev = max((k for k in keys if k < iso), default=None)
        nxt = min((k for k in keys if k > iso), default=None)
        return (date.fromisoformat(prev) if prev else None,
                date.fromisoformat(nxt) if nxt else None)

    def available(self) -> list[date]:
        return [date.fromisoformat(k) for k in sorted(self.days)]


def human_date(d: date) -> str:
    return f"{d.day} {TR_MONTH_NAMES[d.month]} {d.year}"


def day_name(d: date) -> str:
    return TR_DAYS[d.weekday()]


if __name__ == "__main__":  # hızlı test: python menu.py kayitli_sayfa.html
    import sys
    src = Path(sys.argv[1]).read_text(encoding="utf-8")
    for dm in parse_menu_html(src):
        print("==", dm.label)
        for s in dm.sections:
            print("  ##", s.title)
            for it in s.items:
                print("    -", it.name, it.kcal or "", "🌾" if it.wheat else "", "🥛" if it.milk else "")
        print("  notes:", len(dm.notes))
