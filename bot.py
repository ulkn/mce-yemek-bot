"""
TUSAŞ Yemek Menüsü Telegram Botu (şirket bilgisayarında çalışır)

  python bot.py              -> botu başlatır (Telegram'dan /yemek bekler)
  python bot.py --login      -> görünür tarayıcı açar, portal oturumunu kaydeder
  python bot.py --test       -> portaldan menüyü çeker, bugünün kartını card_test.png'ye yazar
  python bot.py --test --html kayitli_sayfa.mhtml   -> kayıtlı sayfadan dener (ağ gerekmez)

Her sabah (varsayılan 08:00) portaldan menüyü yeniden çekip o günün kartını kendiliğinden gönderir.
Ayarlar: config.json → "gunluk_bildirim"; Telegram'dan /bildirim ile aç/kapat.

Telegram'a ulaşma şekli config.json → telegram_mode: relay | github | direct  (bkz. transports.py)
"""
from __future__ import annotations

import argparse
import email
import hashlib
import json
import logging
import re
import socket
import sys
import time
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from card import build_html, build_text, render_png  # noqa: E402
from netfix import setup_ssl  # noqa: E402
from transports import TelegramError, make_transport  # noqa: E402
from menu import (LoginRequired, MenuStore, TR_MONTHS, day_name,  # noqa: E402
                  human_date, interactive_login, tr_lower)

log = logging.getLogger("yemekbot")

WEEKDAYS = {"pazartesi": 0, "sali": 1, "carsamba": 2, "persembe": 3, "cuma": 4, "cumartesi": 5, "pazar": 6}
SHORT_DAYS = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]


# --------------------------------------------------------------------------- #
# Ayarlar
# --------------------------------------------------------------------------- #
def load_config() -> dict:
    path = BASE / "config.json"
    if not path.exists():
        sys.exit("config.json bulunamadı. config.example.json dosyasını config.json olarak kopyalayıp doldur.")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    # Göreli yolları uygulama klasörüne bağla
    for k, default in [("browser_profile_dir", "browser_profile"), ("cookie_file", "portal_cookies.json"),
                       ("cache_file", "menu_cache.json")]:
        p = Path(cfg.get(k, default))
        cfg[k] = str(p if p.is_absolute() else BASE / p)
    return cfg


# --------------------------------------------------------------------------- #
# "yarın", "cuma", "9 kasım", "09.11" ... -> tarih
# --------------------------------------------------------------------------- #
def _ascii(s: str) -> str:
    return tr_lower(s).translate(str.maketrans("çğıöşüâî", "cgiosuai"))


def parse_day_query(text: str, today: date) -> date | None:
    q = _ascii(text).strip()
    q = re.sub(r"^/\w+(@\w+)?", "", q).strip()   # komutu at
    if q in ("", "bugun", "bu gun"):
        return today
    if q == "yarin":
        return today + timedelta(days=1)
    if q in ("obur gun", "ertesi gun", "yarindan sonra"):
        return today + timedelta(days=2)
    if q == "dun":
        return today - timedelta(days=1)

    m = re.fullmatch(r"([+-]\d{1,2})", q)
    if m:
        return today + timedelta(days=int(m.group(1)))

    next_week = q.startswith(("haftaya", "gelecek hafta", "sonraki hafta"))
    last = q.startswith(("gecen", "onceki"))
    for token in q.split():
        if token in WEEKDAYS:
            wd = WEEKDAYS[token]
            if next_week:   # bir sonraki takvim haftasının o günü
                return today - timedelta(days=today.weekday()) + timedelta(days=7 + wd)
            delta = (wd - today.weekday()) % 7
            if last:        # en son geçen o gün
                delta = delta - 7 if delta else -7
            return today + timedelta(days=delta)

    # 9 kasım [2026]
    m = re.fullmatch(r"(\d{1,2})\s+([a-z]+)(?:\s+(\d{4}))?", q)
    if m:
        months_ascii = {_ascii(k): v for k, v in TR_MONTHS.items()}
        mon = next((v for k, v in months_ascii.items() if k.startswith(m.group(2)[:3])), None)
        if mon:
            return _safe_date(int(m.group(3)) if m.group(3) else None, mon, int(m.group(1)), today)

    # 09.11 / 9/11 / 09-11-2026
    m = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?", q)
    if m:
        y = m.group(3)
        y = int(y) + (2000 if y and len(y) == 2 else 0) if y else None
        return _safe_date(y, int(m.group(2)), int(m.group(1)), today)

    # sadece gün: "9"
    m = re.fullmatch(r"(\d{1,2})", q)
    if m:
        return _safe_date(None, today.month, int(m.group(1)), today)
    return None


def _safe_date(year: int | None, month: int, day: int, today: date) -> date | None:
    try:
        if year:
            return date(year, month, day)
        d = date(today.year, month, day)
        # Aralık'ta "3 ocak" yazılırsa gelecek yıl kabul et
        if (d - today).days < -180:
            d = date(today.year + 1, month, day)
        elif (d - today).days > 180:
            d = date(today.year - 1, month, day)
        return d
    except ValueError:
        return None


def _parse_hhmm(value, default: str) -> dtime:
    for v in (value, default):
        m = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", str(v or ""))
        if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
            return dtime(int(m.group(1)), int(m.group(2)))
    return dtime(8, 0)


# --------------------------------------------------------------------------- #
# Telegram
# --------------------------------------------------------------------------- #
class YemekBot:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.tg = make_transport(cfg)
        self.max_age = int(cfg.get("max_age_minutes", 30)) * 60
        self.store = MenuStore(cfg)
        self.allowed = {str(x) for x in cfg.get("allowed_chat_ids", [])}
        self.png_cache: dict[str, bytes] = {}

        # ---- her sabah otomatik gönderim ----
        dn = cfg.get("gunluk_bildirim") or {}
        self.daily_cfg_on = bool(dn.get("aktif", True))
        self.daily_at = _parse_hhmm(dn.get("saat"), "08:00")
        self.daily_until = _parse_hhmm(dn.get("son_saat"), "11:00")
        self.daily_retry = max(1, int(dn.get("tekrar_dakika", 10))) * 60
        self.daily_weekdays_only = bool(dn.get("sadece_hafta_ici", True))
        self.state_file = BASE / "gunluk_durum.json"
        self.state = self._load_state()
        self._daily_next_try = 0.0
        self._daily_warned: date | None = None

    # ---- kalıcı durum (son gönderim tarihi, /bildirim aç/kapat) ----
    def _load_state(self) -> dict:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_state(self):
        try:
            self.state_file.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as e:
            log.warning("gunluk_durum.json yazılamadı: %s", e)

    def _daily_on(self) -> bool:
        return self.state.get("aktif", self.daily_cfg_on)

    # ---- menü + kart ----
    def _ensure_menu(self, force: bool = False) -> str | None:
        """Menüyü tazele. Hata olursa kullanıcıya gösterilecek uyarıyı döndür."""
        try:
            self.store.refresh(force=force)
            return None
        except LoginRequired as e:
            log.warning("Oturum gerekli: %s", e)
            return ("🔐 Portal oturumu düşmüş görünüyor.\n"
                    "Bilgisayarda <b>giris_yap.bat</b> dosyasını çalıştırıp portala bir kez giriş yap.")
        except Exception as e:  # ağ, tarayıcı vs.
            log.exception("Menü çekilemedi")
            return f"⚠️ Menü portaldan çekilemedi: <code>{type(e).__name__}</code>"

    def _card(self, d: date) -> bytes:
        menu = self.store.get(d)
        today = date.today()
        html = build_html(menu, self.store.title, today, self.cfg.get("card_theme", "acik"))
        key = hashlib.md5(html.encode("utf-8")).hexdigest()
        if key not in self.png_cache:
            if len(self.png_cache) > 30:
                self.png_cache.clear()
            self.png_cache[key] = render_png(html, self.cfg)
        return self.png_cache[key]

    def _keyboard(self, d: date) -> dict:
        prev, nxt = self.store.neighbors(d)
        row = []
        if prev:
            row.append({"text": f"◀️ {prev.day} {SHORT_DAYS[prev.weekday()]}", "callback_data": f"d:{prev.isoformat()}"})
        if d != date.today() and self.store.get(date.today()):
            row.append({"text": "📍 Bugün", "callback_data": f"d:{date.today().isoformat()}"})
        if nxt:
            row.append({"text": f"{nxt.day} {SHORT_DAYS[nxt.weekday()]} ▶️", "callback_data": f"d:{nxt.isoformat()}"})
        return {"inline_keyboard": [row, [{"text": "🗓️ Tüm günler", "callback_data": "week"}]]}

    def _caption(self, d: date, warn: str | None) -> str:
        cap = f"🍽️ <b>{human_date(d)} · {day_name(d)}</b>"
        if warn:
            cap += "\n\n" + warn + "\n<i>(önbellekteki menü gösteriliyor)</i>"
        return cap

    def send_menu(self, chat_id, d: date, edit_message_id: int | None = None):
        if not edit_message_id:
            self.tg.call("sendChatAction", chat_id=chat_id, action="upload_photo")
        warn = self._ensure_menu()
        if warn and not self.store.days:
            return self.tg.call("sendMessage", chat_id=chat_id, text=warn, parse_mode="HTML")

        if not self.store.get(d):
            # Sayfa yeni haftayı yayınlamış olabilir -> bir kez zorla yenile
            if not warn and d >= date.today():
                warn = self._ensure_menu(force=True)
        menu = self.store.get(d)
        if not menu:
            days = self.store.available()
            rng = f"{human_date(days[0])} – {human_date(days[-1])}" if days else "-"
            return self.tg.call(
                "sendMessage", chat_id=chat_id, parse_mode="HTML",
                text=f"🤷 <b>{human_date(d)} {day_name(d)}</b> için menü yok.\n"
                     f"Portalda görünen aralık: {rng}",
                reply_markup=self._week_keyboard())

        return self._deliver(chat_id, d, menu, warn, edit_message_id)

    def _deliver(self, chat_id, d: date, menu, warn: str | None,
                 edit_message_id: int | None = None, header: str = ""):
        caption = header + self._caption(d, warn)
        kb = self._keyboard(d)
        try:
            png = self._card(d)
        except Exception:
            log.exception("Kart görseli üretilemedi, metin gönderiliyor")
            return self.tg.call("sendMessage", chat_id=chat_id, parse_mode="HTML",
                                text=build_text(menu, self.store.title), reply_markup=kb)

        files = {"photo": (f"menu_{d.isoformat()}.jpg", png, "image/jpeg")}
        if edit_message_id:
            media = {"type": "photo", "media": "attach://photo", "caption": caption, "parse_mode": "HTML"}
            return self.tg.call("editMessageMedia", chat_id=chat_id, message_id=edit_message_id,
                                media=media, reply_markup=kb, files=files, http_timeout=60)
        return self.tg.call("sendPhoto", chat_id=chat_id, caption=caption, parse_mode="HTML",
                            reply_markup=kb, files=files, http_timeout=60)

    # ---- her sabah otomatik menü ----
    def check_daily(self, now: datetime | None = None):
        """Ana döngüde sık sık çağrılır. Saati geldiyse bugünün menüsünü bir kez gönderir."""
        if not self._daily_on() or not self.allowed:
            return
        now = now or datetime.now()
        today = now.date()
        if self.state.get("son_gonderim") == today.isoformat():
            return
        if self.daily_weekdays_only and today.weekday() >= 5:
            return
        start = datetime.combine(today, self.daily_at)
        end = datetime.combine(today, self.daily_until)
        if end <= start:
            end = start + timedelta(hours=3)
        if not (start <= now < end) or time.time() < self._daily_next_try:
            return
        self._daily_next_try = time.time() + self.daily_retry
        final = now + timedelta(seconds=self.daily_retry) >= end   # son deneme mi?
        self.send_daily(today, final=final)

    def send_daily(self, today: date, final: bool = True, mark: bool = True) -> bool:
        log.info("Günlük menü: portal kontrol ediliyor (%s)", today.isoformat())
        warn = self._ensure_menu(force=True)      # her sabah güncel sürümü çek
        menu = self.store.get(today)

        if warn and not final:
            # Portal okunamadı: bir kez haber ver, sonra sessizce tekrar dene
            if self._daily_warned != today:
                self._daily_warned = today
                self._broadcast(warn + f"\n\n⏰ Sabah menüsü için {self.daily_retry // 60} dk sonra tekrar deneyeceğim.")
            return False

        if not menu:
            published_after = any(d > today for d in self.store.available())
            if not final and not published_after and today.weekday() < 5:
                log.info("Bugünün menüsü portalda henüz yok, sonra tekrar denenecek")
                return False
            if today.weekday() < 5:
                self._broadcast(f"🤷 <b>{human_date(today)} {day_name(today)}</b> için portalda menü bulunamadı.")
            if mark:
                self._mark_daily(today)
            return True

        ok = False
        for cid in self.allowed:
            try:
                self._deliver(cid, today, menu, warn, header="☀️ <b>Günaydın! Bugünün menüsü</b>\n")
                ok = True
            except Exception:
                log.exception("Günlük menü %s sohbetine gönderilemedi", cid)
        if ok:
            log.info("Günlük menü gönderildi")
            if mark:
                self._mark_daily(today)
        return ok

    def _mark_daily(self, d: date):
        self.state["son_gonderim"] = d.isoformat()
        self._save_state()

    def _broadcast(self, text: str):
        for cid in self.allowed:
            try:
                self.tg.call("sendMessage", chat_id=cid, text=text, parse_mode="HTML")
            except Exception as e:
                log.warning("Mesaj gönderilemedi (%s): %s", cid, e)

    def _daily_status(self) -> str:
        on = self._daily_on()
        days = "hafta içi her gün" if self.daily_weekdays_only else "her gün"
        last = self.state.get("son_gonderim")
        return (f"⏰ <b>Sabah menüsü: {'AÇIK ✅' if on else 'KAPALI ⛔'}</b>\n"
                f"Saat: {self.daily_at.strftime('%H:%M')} ({days})\n"
                f"Bilgisayar o saatte kapalıysa {self.daily_until.strftime('%H:%M')}'e kadar açıldığında gönderilir.\n"
                f"Son gönderim: {human_date(date.fromisoformat(last)) if last else '-'}\n\n"
                "<code>/bildirim kapat</code> · <code>/bildirim aç</code> · <code>/bildirim şimdi</code>")

    def handle_bildirim(self, chat_id, text: str):
        arg = _ascii(" ".join(text.split()[1:]))
        if arg in ("kapat", "kapali", "durdur", "off"):
            self.state["aktif"] = False
            self._save_state()
        elif arg in ("ac", "acik", "baslat", "on"):
            self.state["aktif"] = True
            self._save_state()
        elif arg in ("simdi", "test", "gonder"):
            self.send_daily(date.today(), final=True, mark=False)
            return
        self.tg.call("sendMessage", chat_id=chat_id, parse_mode="HTML", text=self._daily_status())

    def _week_keyboard(self) -> dict:
        btns = [{"text": f"{'📍 ' if d == date.today() else ''}{d.day} {SHORT_DAYS[d.weekday()]}",
                 "callback_data": f"n:{d.isoformat()}"}
                for d in self.store.available() if d >= date.today() - timedelta(days=7)]
        return {"inline_keyboard": [btns[i:i + 4] for i in range(0, len(btns), 4)]}

    # ---- güncellemeler ----
    def handle_message(self, msg: dict):
        chat_id = msg["chat"]["id"]
        text = (msg.get("text") or "").strip()
        if not text:
            return
        if str(chat_id) not in self.allowed:
            log.info("Yetkisiz sohbet %s: %s", chat_id, text)
            self.tg.call("sendMessage", chat_id=chat_id, parse_mode="HTML",
                         text=f"⛔ Bu bot özel.\nSohbet kimliğin: <code>{chat_id}</code>\n"
                              f"Kendi botunsa bu sayıyı config.json → allowed_chat_ids içine ekle.")
            return

        cmd = text.split()[0].split("@")[0].lower()
        today = date.today()
        if cmd in ("/start", "/help", "/yardim"):
            self.tg.call("sendMessage", chat_id=chat_id, parse_mode="HTML", text=HELP)
        elif cmd == "/yenile":
            warn = self._ensure_menu(force=True)
            days = self.store.available()
            self.tg.call("sendMessage", chat_id=chat_id, parse_mode="HTML",
                         text=warn or f"🔄 Menü yenilendi: {human_date(days[0])} – {human_date(days[-1])}")
        elif cmd in ("/bildirim", "/sabah"):
            self.handle_bildirim(chat_id, text)
        elif cmd in ("/hafta", "/gunler"):
            warn = self._ensure_menu()
            self.tg.call("sendMessage", chat_id=chat_id, parse_mode="HTML",
                         text=(warn + "\n\n" if warn else "") + "🗓️ Hangi günün menüsü?",
                         reply_markup=self._week_keyboard())
        elif cmd in ("/yemek", "/menu", "/menü"):
            d = parse_day_query(text, today)
            if not d:
                self.tg.call("sendMessage", chat_id=chat_id, parse_mode="HTML",
                             text="🤔 Günü anlayamadım. Örnek: <code>/yemek yarın</code>, "
                                  "<code>/yemek cuma</code>, <code>/yemek 12.11</code>",
                             reply_markup=self._week_keyboard() if self.store.days else None)
                return
            self.send_menu(chat_id, d)
        else:
            d = parse_day_query(text, today) if not text.startswith("/") else None
            if d:  # komutsuz "yarın", "cuma" yazılırsa da çalışsın
                self.send_menu(chat_id, d)

    def handle_callback(self, cq: dict):
        chat_id = cq["message"]["chat"]["id"]
        if str(chat_id) not in self.allowed:
            return
        data = cq.get("data", "")
        try:
            self.tg.call("answerCallbackQuery", callback_query_id=cq["id"], text="⏳ Hazırlanıyor...")
        except Exception:
            pass
        if data == "week":
            self.tg.call("sendMessage", chat_id=chat_id, text="🗓️ Hangi günün menüsü?",
                         reply_markup=self._week_keyboard())
            return
        kind, _, iso = data.partition(":")
        d = date.fromisoformat(iso)
        if kind == "d" and cq["message"].get("photo"):
            try:
                self.send_menu(chat_id, d, edit_message_id=cq["message"]["message_id"])
                return
            except TelegramError as e:
                if "not modified" in str(e):
                    return
                log.warning("Düzenleme başarısız, yeni mesaj atılıyor: %s", e)
        self.send_menu(chat_id, d)

    def run(self):
        me = self.tg.call("getMe")
        log.info("Bot başladı: @%s (mod: %s)", me.get("username"), self.cfg.get("telegram_mode", "relay"))
        if self.cfg.get("telegram_mode", "relay") != "github":  # GitHub modunda Worker /setup yapıyor
            try:
                self.tg.call("setMyCommands", commands=COMMANDS)
            except Exception as e:
                log.warning("setMyCommands: %s", e)
        # Açılışta menüyü bir kez çek (oturum sorunu varsa hemen haber ver)
        warn = self._ensure_menu()
        if warn:
            for cid in self.allowed:
                try:
                    self.tg.call("sendMessage", chat_id=cid, text=warn, parse_mode="HTML")
                except Exception:
                    pass

        if self._daily_on():
            log.info("Sabah menüsü açık: her %s %s", "hafta içi" if self.daily_weekdays_only else "gün",
                     self.daily_at.strftime("%H:%M"))
        offset = None
        while True:
            try:
                self.check_daily()
            except Exception:
                log.exception("Günlük menü kontrolü başarısız")
            try:
                updates = self.tg.get_updates(offset)
            except Exception as e:
                log.warning("Güncelleme alınamadı: %s — 10 sn sonra tekrar", e)
                time.sleep(10)
                continue
            for u in updates:
                offset = u["update_id"] + 1
                try:
                    ts = self.tg.received_at(u)
                    if ts and time.time() - ts > self.max_age:
                        log.info("Eski talep atlandı (%d dk önce)", (time.time() - ts) // 60)
                    elif "message" in u:
                        self.handle_message(u["message"])
                    elif "callback_query" in u:
                        self.handle_callback(u["callback_query"])
                except Exception:
                    log.exception("Güncelleme işlenemedi")
                finally:
                    try:
                        self.tg.ack(u)
                    except Exception as e:
                        log.warning("ack hatası: %s", e)

COMMANDS = [
    {"command": "yemek", "description": "Menü: /yemek [bugün|yarın|cuma|12.11]"},
    {"command": "hafta", "description": "Menüsü olan günleri listele"},
    {"command": "yenile", "description": "Menüyü portaldan yeniden çek"},
    {"command": "bildirim", "description": "Sabah 08:00 menüsü: durum / aç / kapat"},
    {"command": "yardim", "description": "Kullanım"},
]

HELP = (
    "🍽️ <b>Yemek Menüsü Botu</b>\n\n"
    "<code>/yemek</code> → bugünün menüsü\n"
    "<code>/yemek yarın</code> · <code>/yemek dün</code>\n"
    "<code>/yemek cuma</code> · <code>/yemek haftaya salı</code>\n"
    "<code>/yemek 12 kasım</code> · <code>/yemek 12.11</code> · <code>/yemek +2</code>\n"
    "<code>/hafta</code> → menüsü olan günler\n"
    "<code>/yenile</code> → portaldan yeniden çek\n"
    "<code>/bildirim</code> → sabah 08:00 menüsü (aç / kapat / şimdi)\n\n"
    "Her sabah 08:00'de bugünün menüsü kendiliğinden gelir.\n"
    "Kartın altındaki ◀️ ▶️ ile günler arasında gezebilirsin."
)


# --------------------------------------------------------------------------- #
def read_saved_page(path: Path) -> str:
    raw = path.read_bytes()
    if path.suffix.lower() in (".mhtml", ".mht"):
        msg = email.message_from_bytes(raw)
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                cs = part.get_content_charset() or "utf-8"
                return part.get_payload(decode=True).decode(cs, errors="replace")
    return raw.decode("utf-8", errors="replace")


def main():
    for stream in (sys.stdout, sys.stderr):
        if stream and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true", help="Portala giriş yapıp oturumu kaydet")
    ap.add_argument("--test", action="store_true", help="Telegram'a göndermeden kart üret")
    ap.add_argument("--html", help="--test için kayıtlı sayfa (.html/.mhtml)")
    ap.add_argument("--gun", default="", help="--test için gün (ör. yarın, cuma, 12.11)")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=([logging.StreamHandler()] if sys.stderr else [])
        + [logging.FileHandler(BASE / "bot.log", encoding="utf-8")])
    cfg = load_config()
    log.info("SSL: %s", setup_ssl(cfg, BASE))

    if args.login:
        ok = interactive_login(cfg)
        sys.exit(0 if ok else 1)

    if args.test:
        store = MenuStore(cfg)
        if args.html:
            store.load_html(read_saved_page(Path(args.html)))
        else:
            store.refresh(force=True)
        days = store.available()
        print(f"✅ {len(days)} günlük menü okundu: {human_date(days[0])} – {human_date(days[-1])}")
        d = parse_day_query(args.gun, date.today()) or date.today()
        menu = store.get(d) or store.get(days[0])
        out = BASE / "card_test.jpg"
        out.write_bytes(render_png(build_html(menu, store.title, theme=cfg.get("card_theme", "acik")), cfg))
        print(f"🖼️ {menu.label} kartı → {out}")
        return

    if cfg.get("telegram_mode", "relay") != "github" and (
            not cfg.get("telegram_token") or "BURAYA" in cfg["telegram_token"]):
        sys.exit("config.json içine telegram_token yaz.")

    # Aynı anda iki kopya çalışmasın (Telegram 409 Conflict verir)
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", int(cfg.get("lock_port", 47651))))
    except OSError:
        sys.exit("Bot zaten çalışıyor.")
    YemekBot(cfg).run()


if __name__ == "__main__":
    main()
