"""
Şirket bilgisayarından hangi servislere ulaşılabildiğini test eder ve hangi modu
kullanman gerektiğini söyler.   Çalıştır: baglanti_testi.bat
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import truststore  # Windows sertifika deposunu kullan (şirket SSL denetimi için)
    truststore.inject_into_ssl()
except Exception:
    pass

import requests

for stream in (sys.stdout, sys.stderr):
    if stream and hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parent
cfg = {}
if (BASE / "config.json").exists():
    cfg = json.loads((BASE / "config.json").read_text(encoding="utf-8"))

s = requests.Session()
if cfg.get("proxy"):
    s.proxies = {"http": cfg["proxy"], "https": cfg["proxy"]}
if cfg.get("ca_bundle"):
    s.verify = cfg["ca_bundle"]

relay = (cfg.get("relay_url") or "").rstrip("/")
targets = [
    ("telegram", "Telegram API (doğrudan)", "https://api.telegram.org/"),
    ("workers", "Cloudflare Workers", (relay + "/health") if relay else "https://connectivity-check.workers.dev/"),
    ("github", "GitHub API", "https://api.github.com/"),
    ("portal", "Yemek portalı", cfg.get("menu_url", "https://erp.tai.com.tr/intranet/portal/meal/1")),
]

ok = {}
print("\n=== Bağlantı testi ===\n")
for key, name, url in targets:
    try:
        r = s.get(url, timeout=12, allow_redirects=False)
        ok[key] = True
        extra = ""
        if key == "workers" and relay:
            extra = " ✔ Worker cevap verdi" if "yemek-bot worker ok" in r.text else f" (Worker cevabı beklenmedik: {r.text[:60]!r})"
        print(f"✅ {name:26} erişilebilir (HTTP {r.status_code}){extra}")
    except requests.exceptions.SSLError as e:
        ok[key] = False
        print(f"⚠️  {name:26} SSL hatası → şirket sertifikası gerekebilir (config.json → ca_bundle)\n     {str(e)[:150]}")
    except requests.RequestException as e:
        ok[key] = False
        print(f"❌ {name:26} ERİŞİLEMİYOR  ({type(e).__name__})")

print("\n=== Öneri ===")
if ok.get("telegram"):
    print('➡️  Telegram doğrudan açık: config.json → "telegram_mode": "direct"')
elif ok.get("workers"):
    print('➡️  "telegram_mode": "relay"  (Cloudflare Worker üzerinden — en hızlı ve basit)')
elif ok.get("github"):
    print('➡️  "telegram_mode": "github"  (GitHub posta kutusu — Worker yine gerekli ama PC ona bağlanmaz)')
else:
    print("➡️  Hiçbir dış servis açık değil. Proxy gerekiyorsa config.json → proxy alanını doldurup tekrar dene.")
if not ok.get("portal"):
    print("⚠️  Portal açılmadı: bu bilgisayar intranete bağlı mı?")
print()
