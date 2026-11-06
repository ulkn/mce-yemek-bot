"""
Şirket bilgisayarından hangi servislere ulaşılabildiğini test eder, şirket proxy'sini
bulmaya çalışır ve hangi modu kullanman gerektiğini söyler.   Çalıştır: baglanti_testi.bat
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

import requests

for stream in (sys.stdout, sys.stderr):
    if stream and hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parent
CFG_PATH = BASE / "config.json"
cfg = json.loads(CFG_PATH.read_text(encoding="utf-8")) if CFG_PATH.exists() else {}
sys.path.insert(0, str(BASE))
from netfix import setup_ssl  # noqa: E402
SSL_INFO = setup_ssl(cfg, BASE)

relay = (cfg.get("relay_url") or "").rstrip("/")
TARGETS = [
    ("telegram", "Telegram API (doğrudan)", "https://api.telegram.org/"),
    ("workers", "Cloudflare Workers", (relay + "/health") if relay else "https://connectivity-check.workers.dev/"),
    ("github", "GitHub API", "https://api.github.com/"),
]
PORTAL = cfg.get("menu_url", "https://erp.tai.com.tr/intranet/portal/meal/1")


def session(proxy: str | None, use_system: bool = True) -> requests.Session:
    s = requests.Session()
    s.trust_env = use_system
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    s.verify = cfg["_verify"]
    return s


def probe(s: requests.Session, url: str) -> tuple[str, str]:
    """('ok'|'auth'|'ssl'|'fail', açıklama)"""
    try:
        r = s.get(url, timeout=10, allow_redirects=False)
        if r.status_code == 407:
            return "auth", "HTTP 407 – proxy kullanıcı adı/şifre istiyor"
        return "ok", f"HTTP {r.status_code}"
    except requests.exceptions.ProxyError as e:
        if "407" in str(e):
            return "auth", "HTTP 407 – proxy kullanıcı adı/şifre istiyor"
        return "fail", "ProxyError: " + _short(e)
    except requests.exceptions.SSLError as e:
        return "ssl", "SSL hatası: " + _short(e)
    except requests.RequestException as e:
        return "fail", f"{type(e).__name__}: " + _short(e)


def _short(e: Exception) -> str:
    m = str(e)
    for key in ("getaddrinfo failed", "Name or service not known", "timed out", "Connection refused",
                "actively refused", "CERTIFICATE_VERIFY_FAILED", "Tunnel connection failed", "407"):
        if key in m:
            return key
    return m[:90]


def find_proxies() -> list[tuple[str, str]]:
    """Bilgisayarda tanımlı proxy adaylarını topla: (kaynak, http://host:port)."""
    found: list[tuple[str, str]] = []

    def add(src, p):
        p = p.strip().strip(";")
        if not p:
            return
        if "=" in p:  # "http=host:port;https=host:port"
            for part in p.split(";"):
                if "=" in part:
                    add(src, part.split("=", 1)[1])
            return
        if not p.startswith("http"):
            p = "http://" + p
        if p not in [x[1] for x in found]:
            found.append((src, p))

    if cfg.get("proxy"):
        add("config.json", cfg["proxy"])
    for k, v in urllib.request.getproxies().items():
        if k in ("http", "https"):
            add("Windows/ortam ayarı", v)

    if sys.platform == "win32":
        # Internet Seçenekleri (kullanıcı) – sabit proxy ve PAC (otomatik yapılandırma betiği)
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
            for name in ("ProxyServer", "AutoConfigURL"):
                try:
                    val, _ = winreg.QueryValueEx(key, name)
                except OSError:
                    continue
                if name == "ProxyServer":
                    add("Internet Seçenekleri", val)
                else:
                    print(f"   PAC betiği bulundu: {val}")
                    for p in pac_proxies(val):
                        add("PAC betiği", p)
        except Exception as e:
            print("   (kayıt defteri okunamadı:", e, ")")
        # WinHTTP
        try:
            out = subprocess.run(["netsh", "winhttp", "show", "proxy"], capture_output=True,
                                 text=True, timeout=10).stdout
            m = re.search(r"(?:Proxy Server\(s\)|Proxy Sunucu\(lar\)ı?)\s*:\s*(\S+)", out, re.I)
            if m:
                add("WinHTTP", m.group(1))
        except Exception:
            pass
    return found


def pac_proxies(pac_url: str) -> list[str]:
    try:
        if pac_url.startswith("file://"):
            text = Path(pac_url[7:].lstrip("/")).read_text(errors="ignore")
        else:
            text = session(None, use_system=False).get(pac_url, timeout=10).text
    except Exception as e:
        print(f"   PAC indirilemedi ({type(e).__name__})")
        return []
    return list(dict.fromkeys(re.findall(r"PROXY\s+([\w.\-]+:\d+)", text)))


def save_proxy(p: str):
    data = json.loads(CFG_PATH.read_text(encoding="utf-8"))
    data["proxy"] = p
    CFG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


ICON = {"ok": "✅", "auth": "🔐", "ssl": "⚠️ ", "fail": "❌"}

print(f"\nSSL: {SSL_INFO}")
print("\n=== 1) Varsayılan ayarlarla test ===\n")
res = {}
s = session(cfg.get("proxy") or None)
for key, name, url in TARGETS:
    st, msg = probe(s, url)
    res[key] = st
    extra = ""
    if key == "workers" and relay and st == "ok":
        try:
            extra = "  ✔ Worker cevap verdi" if "yemek-bot worker ok" in s.get(url, timeout=10).text else ""
        except Exception:
            pass
    print(f"{ICON[st]} {name:26} {msg}{extra}")
st, msg = probe(session(None, use_system=False), PORTAL)
print(f"{ICON[st]} {'Yemek portalı':26} {msg}")
portal_ok = st == "ok"

best_proxy = None
if not any(v == "ok" for v in res.values()):
    print("\n=== 2) Şirket proxy'si aranıyor ===\n")
    cands = find_proxies()
    if not cands:
        print("   Bilgisayarda tanımlı proxy bulunamadı.")
    for src, p in cands:
        ps = session(p, use_system=False)
        got = {key: probe(ps, url)[0] for key, _name, url in TARGETS}
        print(f"   {p:38} ({src}):  " + "  ".join(f"{ICON[v]} {k}" for k, v in got.items()))
        if best_proxy is None and "ok" in got.values():
            best_proxy = p
            res.update(got)
        elif best_proxy is None:
            for k, v in got.items():
                if v == "auth":
                    res[k] = "auth"

print("\n=== Öneri ===")
if best_proxy:
    print(f"➡️  Şirket proxy'si çalışıyor: {best_proxy}")
    if CFG_PATH.exists() and cfg.get("proxy") != best_proxy:
        ans = input("   config.json'a yazayım mı? (E/H): ").strip().lower()
        if ans in ("e", "evet", "y", "yes"):
            save_proxy(best_proxy)
            print("   ✔ config.json → proxy kaydedildi.")
        else:
            print(f'   Elle yazmak için: "proxy": "{best_proxy}"')
if res.get("telegram") == "ok":
    print('➡️  "telegram_mode": "direct"   (Telegram doğrudan açık)')
elif res.get("workers") == "ok":
    print('➡️  "telegram_mode": "relay"    (Cloudflare Worker üzerinden — en hızlı)')
elif res.get("github") == "ok":
    print('➡️  "telegram_mode": "github"   (GitHub posta kutusu)')
elif "ssl" in res.values():
    print("⚠️  SSL hatası sürüyor. Son çare olarak doğrulamayı kapatabilirsin:")
    print('   config.json → "ssl_verify": false   (sonra bu testi tekrar çalıştır)')
elif "auth" in res.values():
    print("🔐 Proxy bulundu ama kullanıcı adı/şifre istiyor. Bu ekranın fotoğrafını gönder, çözümü (px) kuralım.")
else:
    print("❌ Python bu bilgisayardan dışarı çıkamıyor.")
    print("   Kontrol: Edge'de https://api.github.com adresini aç.")
    print("   • Sayfada { ... } ile başlayan yazılar görünüyorsa → tarayıcı çıkıyor, Python çıkamıyor (proxy sorunu).")
    print("     Bu ekranın fotoğrafını gönder.")
    print("   • Sayfa açılmıyorsa → bu bilgisayarın internet erişimi yok; bot internete çıkabilen bir bilgisayarda çalışmalı.")
if not portal_ok:
    print("⚠️  Portal da açılmadı: bu bilgisayar intranete bağlı mı?")
print()
