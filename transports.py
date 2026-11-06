"""
Telegram'a nasıl ulaşılacağı (config.json → "telegram_mode"):

  "relay"  : Cloudflare Worker üzerinden (PC → https://xxx.workers.dev/bot<TOKEN>/... → Telegram)
  "github" : GitHub repo posta kutusu (Telegram → Worker → repo/inbox → PC → repo/outbox → Worker → Telegram)
  "direct" : doğrudan api.telegram.org (erişim varsa)
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
import uuid

import requests

log = logging.getLogger("yemekbot")


def _session(cfg: dict) -> requests.Session:
    s = requests.Session()
    if cfg.get("proxy"):
        s.proxies = {"http": cfg["proxy"], "https": cfg["proxy"]}
    if cfg.get("ca_bundle"):
        s.verify = cfg["ca_bundle"]
    return s


class TelegramError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
class HttpTelegram:
    """Doğrudan veya Worker relay üzerinden Telegram Bot API."""

    def __init__(self, cfg: dict, base_url: str):
        self.api = f"{base_url.rstrip('/')}/bot{cfg['telegram_token']}/"
        self.s = _session(cfg)

    def call(self, method: str, http_timeout: int = 30, files=None, **params):
        data = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                for k, v in params.items() if v is not None}
        r = self.s.post(self.api + method, data=data, files=files, timeout=http_timeout)
        try:
            j = r.json()
        except ValueError:
            raise TelegramError(f"{method}: HTTP {r.status_code} {r.text[:200]}")
        if not j.get("ok"):
            raise TelegramError(f"{method}: {j.get('description')}")
        return j["result"]

    def get_updates(self, offset):
        return self.call("getUpdates", http_timeout=70, timeout=50, offset=offset,
                         allowed_updates=["message", "callback_query"])

    def ack(self, update):
        pass

    def received_at(self, update) -> float | None:
        msg = update.get("message")
        return float(msg["date"]) if msg and "date" in msg else None


# --------------------------------------------------------------------------- #
class GitHubMailbox:
    """Telegram çağrılarını GitHub reposundaki inbox/outbox dosyalarıyla taşır."""

    def __init__(self, cfg: dict):
        g = cfg["github"]
        self.repo = g["repo"]
        self.branch = g.get("branch", "main")
        self.api = g.get("api", "https://api.github.com").rstrip("/")
        self.poll = float(g.get("poll_seconds", 6))
        self.s = _session(cfg)
        self.s.headers.update({
            "Authorization": f"Bearer {g['token']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "yemek-bot-pc",
        })
        self._etag = None
        self._seen: list[str] = []          # GitHub listesi gecikmeli güncellenirse çift işlemeyi önler
        self._known: set[str] = set()
        self._load_known_cards()

    # ---- GitHub yardımcıları ----
    def _url(self, path: str) -> str:
        return f"{self.api}/repos/{self.repo}/contents/{path}"

    def _put(self, path: str, data: bytes, message: str) -> int:
        r = self.s.put(self._url(path), timeout=60, json={
            "message": message, "branch": self.branch,
            "content": base64.b64encode(data).decode("ascii")})
        if r.status_code not in (200, 201, 422):
            raise TelegramError(f"GitHub PUT {path}: {r.status_code} {r.text[:200]}")
        return r.status_code

    def _load_known_cards(self):
        try:
            r = self.s.get(self._url("cards"), params={"ref": self.branch}, timeout=30)
            if r.ok:
                self._known = {f["path"] for f in r.json()}
        except requests.RequestException as e:
            log.warning("cards listesi alınamadı: %s", e)

    # ---- Telegram arayüzü ----
    def get_updates(self, offset):
        time.sleep(self.poll)
        headers = {"If-None-Match": self._etag} if self._etag else {}
        r = self.s.get(self._url("inbox"), params={"ref": self.branch}, headers=headers, timeout=30)
        if r.status_code in (304, 404):
            return []
        if not r.ok:
            raise TelegramError(f"GitHub inbox: {r.status_code} {r.text[:200]}")
        self._etag = r.headers.get("ETag")
        updates = []
        for f in sorted(r.json(), key=lambda x: x["name"]):
            if f.get("type") != "file" or not f["name"].endswith(".json") or f["path"] in self._seen:
                continue
            raw = self.s.get(self._url(f["path"]), params={"ref": self.branch}, timeout=30,
                             headers={"Accept": "application/vnd.github.raw"})
            if not raw.ok:
                continue
            u = raw.json()
            u["_gh"] = {"path": f["path"], "sha": f["sha"]}
            updates.append(u)
        if updates:
            self._etag = None  # silinince liste yeniden okunsun
        return updates

    def ack(self, update):
        meta = update.get("_gh")
        if not meta:
            return
        self._seen = (self._seen + [meta["path"]])[-200:]
        r = self.s.delete(self._url(meta["path"]), timeout=30, json={
            "message": f"işlendi {meta['path']}", "sha": meta["sha"], "branch": self.branch})
        if r.status_code not in (200, 404, 409, 422):
            log.warning("inbox silinemedi %s: %s", meta["path"], r.status_code)

    def received_at(self, update) -> float | None:
        ms = update.get("_received")
        return ms / 1000 if ms else None

    def call(self, method: str, http_timeout: int = 30, files=None, **params):
        if method in ("sendChatAction", "answerCallbackQuery"):
            return True                       # Worker bunu zaten anında yaptı
        if method == "getMe":
            return {"username": f"github:{self.repo}"}

        specs = {}
        for field, (fname, data, mime) in (files or {}).items():
            ext = "." + fname.rsplit(".", 1)[-1] if "." in fname else ""
            path = f"cards/{hashlib.sha256(data).hexdigest()[:24]}{ext}"
            if path not in self._known:
                self._put(path, data, f"kart {fname}")
                self._known.add(path)
            specs[field] = {"path": path, "filename": fname, "mime": mime}

        clean = {k: v for k, v in params.items() if v is not None}
        doc = {"ops": [{"method": method, "params": clean, "files": specs}]}
        name = f"outbox/{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}.json"
        self._put(name, json.dumps(doc, ensure_ascii=False).encode("utf-8"), f"outbox {method}")
        return {}


# --------------------------------------------------------------------------- #
def make_transport(cfg: dict):
    mode = cfg.get("telegram_mode", "relay")
    if mode == "github":
        return GitHubMailbox(cfg)
    if mode == "relay":
        if not cfg.get("relay_url"):
            raise SystemExit("config.json → relay_url boş (ör. https://yemek-bot.kullanici.workers.dev)")
        return HttpTelegram(cfg, cfg["relay_url"])
    return HttpTelegram(cfg, "https://api.telegram.org")
