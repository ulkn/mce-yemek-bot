/**
 * Yemek Botu – Cloudflare Worker aracısı
 *
 * İki çalışma şekli var (ikisi aynı Worker'da):
 *
 *  A) RELAY  (şirket PC'si *.workers.dev'e erişebiliyorsa)
 *     PC  --getUpdates/sendPhoto-->  Worker /bot<TOKEN>/...  -->  api.telegram.org
 *     Worker yalnızca BOT_TOKEN'a ait istekleri aynen iletir.
 *
 *  B) GITHUB POSTA KUTUSU  (PC yalnızca api.github.com'a erişebiliyorsa)
 *     Telegram --webhook--> Worker /telegram --> repo/inbox/*.json  --> PC okur
 *     PC --> repo/outbox/*.json (+ cards/*.jpg) --> (GitHub push webhook /github veya dakikalık cron)
 *        --> Worker --> Telegram
 *
 * Ortam değişkenleri (Settings → Variables and Secrets):
 *   BOT_TOKEN            (secret)  BotFather token
 *   WEBHOOK_SECRET       (secret)  rastgele uzun bir metin; /setup?key=... için de kullanılır
 *   ALLOWED_CHAT_IDS     (text)    "123456789,987654321"  (boşsa filtre PC'de yapılır)
 *   --- yalnızca GitHub modu ---
 *   GITHUB_REPO          (text)    "ulkn/mce-yemek-bot"  (wrangler.toml'da tanımlı)
 *   GITHUB_TOKEN         (secret)  fine-grained token, bu repoda Contents: Read & Write
 *   GITHUB_BRANCH        (text)    "posta"  (posta kutusu dalı; wrangler.toml'da tanımlı)
 *   GITHUB_WEBHOOK_SECRET(secret)  (isteğe bağlı) repo webhook imza anahtarı
 *   KV binding adı "KV"  (isteğe bağlı) Telegram file_id önbelleği + çift gönderim koruması
 *   Cron Trigger: "* * * * *"  (isteğe bağlı; kurulamazsa her yeni Telegram mesajında da kontrol edilir)
 */

const COMMANDS = [
  { command: "yemek", description: "Menü: /yemek [bugün|yarın|cuma|12.11]" },
  { command: "hafta", description: "Menüsü olan günleri listele" },
  { command: "yenile", description: "Menüyü portaldan yeniden çek" },
  { command: "yardim", description: "Kullanım" },
];

export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    const p = url.pathname;
    try {
      if (p === "/" || p === "/health") return text("yemek-bot worker ok");
      if (p.startsWith("/bot")) return await relay(req, env, url);
      if (p === "/telegram" && req.method === "POST") return await onTelegram(req, env, ctx);
      if (p === "/github" && req.method === "POST") return await onGithub(req, env, ctx);
      if (p === "/setup") return await setup(url, env);
      return text("not found", 404);
    } catch (e) {
      console.log("HATA", p, e && e.stack || e);
      return text("error: " + (e && e.message), 500);
    }
  },

  async scheduled(event, env, ctx) {
    if (githubEnabled(env)) ctx.waitUntil(processOutbox(env, { minAgeMs: 45_000 }));
  },
};

// --------------------------------------------------------------------------
// A) RELAY
// --------------------------------------------------------------------------
async function relay(req, env, url) {
  const m = url.pathname.match(/^\/bot([^/]+)\/([A-Za-z]+)$/);
  if (!m || !env.BOT_TOKEN || m[1] !== env.BOT_TOKEN) return text("forbidden", 403);
  const target = tgBase(env) + url.pathname + url.search;
  const headers = new Headers();
  const ct = req.headers.get("content-type");
  if (ct) headers.set("content-type", ct);
  const body = ["GET", "HEAD"].includes(req.method) ? undefined : await req.arrayBuffer();
  const r = await fetch(target, { method: req.method, headers, body });
  return new Response(r.body, {
    status: r.status,
    headers: { "content-type": r.headers.get("content-type") || "application/json" },
  });
}

// --------------------------------------------------------------------------
// B) GITHUB POSTA KUTUSU
// --------------------------------------------------------------------------
async function onTelegram(req, env, ctx) {
  if (req.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) return text("forbidden", 403);
  if (!githubEnabled(env)) return text("github modu kapalı", 200);

  const u = await req.json();
  const chatId = u.message?.chat?.id ?? u.callback_query?.message?.chat?.id;
  if (!chatId) return text("ok");

  const allowed = allowedSet(env);
  if (allowed.size && !allowed.has(String(chatId))) {
    if (u.message) {
      await tg(env, "sendMessage", {
        chat_id: chatId, parse_mode: "HTML",
        text: `⛔ Bu bot özel.\nSohbet kimliğin: <code>${chatId}</code>`,
      }).catch(() => {});
    }
    return text("ok");
  }

  // Kullanıcıya anında geri bildirim (PC cevabı birkaç saniye sürebilir)
  if (u.callback_query) {
    ctx.waitUntil(tg(env, "answerCallbackQuery", { callback_query_id: u.callback_query.id, text: "⏳ Hazırlanıyor..." }).catch(() => {}));
  } else if (u.message?.text) {
    ctx.waitUntil(tg(env, "sendChatAction", { chat_id: chatId, action: "upload_photo" }).catch(() => {}));
  }

  const now = Date.now();
  u._received = now;
  await ghPutText(env, `inbox/${now}-${u.update_id}.json`, JSON.stringify(u), `inbox ${u.update_id}`);

  // Cron kurulamazsa yedek: webhook'u kaçırılmış (30 sn'den eski) cevapları şimdi gönder
  ctx.waitUntil(processOutbox(env, { minAgeMs: 30_000 }).catch(e => console.log("outbox yedek", e.message)));

  // PC kapalı mı? Bekleyen eski bir talep varsa kullanıcıyı uyar.
  ctx.waitUntil((async () => {
    const items = await ghList(env, "inbox");
    const oldest = Math.min(...items.map(f => parseInt(f.name, 10)).filter(Number.isFinite));
    if (Number.isFinite(oldest) && now - oldest > 120_000) {
      await tg(env, "sendMessage", {
        chat_id: chatId,
        text: "💤 Şirket bilgisayarı şu an talepleri almıyor gibi görünüyor (kapalı / uykuda olabilir). Açılınca yanıtlanacak.",
      });
    }
  })().catch(e => console.log("inbox kontrol", e.message)));

  return text("ok");
}

async function onGithub(req, env, ctx) {
  if (!githubEnabled(env)) return text("github modu kapalı");
  const raw = await req.text();
  if (env.GITHUB_WEBHOOK_SECRET) {
    const sig = req.headers.get("X-Hub-Signature-256") || "";
    const expected = "sha256=" + (await hmacHex(env.GITHUB_WEBHOOK_SECRET, raw));
    if (sig !== expected) return text("bad signature", 401);
  }
  if (req.headers.get("X-GitHub-Event") !== "push") return text("ok");
  let touchesOutbox = true;
  try {
    const payload = JSON.parse(raw);
    touchesOutbox = (payload.commits || []).some(c => (c.added || []).some(f => f.startsWith("outbox/")));
  } catch (_) { /* yine de işle */ }
  if (touchesOutbox) ctx.waitUntil(processOutbox(env, {}));
  return text("ok");
}

async function processOutbox(env, { minAgeMs = 0 }) {
  const files = (await ghList(env, "outbox"))
    .filter(f => f.type === "file" && f.name.endsWith(".json"))
    .sort((a, b) => a.name.localeCompare(b.name));
  for (const f of files) {
    const ts = parseInt(f.name, 10);
    if (minAgeMs && Number.isFinite(ts) && Date.now() - ts < minAgeMs) continue;
    if (env.KV) {
      if (await env.KV.get("done:" + f.name)) { await ghDelete(env, f.path, f.sha).catch(() => {}); continue; }
      await env.KV.put("done:" + f.name, "1", { expirationTtl: 86400 });
    }
    try {
      const doc = JSON.parse(new TextDecoder().decode(await ghRaw(env, f.path)));
      for (const op of doc.ops || []) {
        try { await runOp(env, op); } catch (e) { console.log("op hatası", op.method, e.message); }
      }
    } catch (e) {
      console.log("outbox okunamadı", f.name, e.message);
    }
    await ghDelete(env, f.path, f.sha).catch(e => console.log("silinemedi", f.name, e.message));
  }
}

async function runOp(env, op) {
  const params = structuredClone(op.params || {});
  const files = op.files || {};
  const form = new FormData();
  let multipart = false;

  for (const [field, spec] of Object.entries(files)) {
    const fid = env.KV ? await env.KV.get("fid:" + spec.path) : null;
    if (fid) {
      if (params.media && params.media.media === `attach://${field}`) params.media.media = fid;
      else params[field] = fid;
    } else {
      const buf = await ghRaw(env, spec.path);
      form.append(field, new Blob([buf], { type: spec.mime || "application/octet-stream" }), spec.filename || field);
      multipart = true;
    }
  }

  let result;
  try {
    if (multipart) {
      for (const [k, v] of Object.entries(params)) form.append(k, typeof v === "object" ? JSON.stringify(v) : String(v));
      result = await tg(env, op.method, null, form);
    } else {
      result = await tg(env, op.method, params);
    }
  } catch (e) {
    if (/not modified/i.test(e.message)) return;
    if (op.method === "editMessageMedia" && params.chat_id) {
      // Düzenlenemiyorsa yeni mesaj olarak gönder
      const media = op.params.media || {};
      return runOp(env, {
        method: "sendPhoto",
        params: { chat_id: params.chat_id, caption: media.caption, parse_mode: media.parse_mode, reply_markup: params.reply_markup },
        files: { photo: files[(media.media || "").replace("attach://", "")] || Object.values(files)[0] },
      });
    }
    throw e;
  }

  // Telegram file_id'yi sakla: aynı kart bir dahaki sefere yeniden yüklenmez
  if (env.KV && result && Array.isArray(result.photo) && result.photo.length) {
    const spec = Object.values(files)[0];
    if (spec) await env.KV.put("fid:" + spec.path, result.photo[result.photo.length - 1].file_id, { expirationTtl: 60 * 86400 });
  }
}

// --------------------------------------------------------------------------
// /setup?key=WEBHOOK_SECRET  → webhook + komut menüsü ayarı
// --------------------------------------------------------------------------
async function setup(url, env) {
  if (!env.WEBHOOK_SECRET || url.searchParams.get("key") !== env.WEBHOOK_SECRET) return text("forbidden", 403);
  const out = { mode: githubEnabled(env) ? "github" : "relay" };
  out.bot = await tg(env, "getMe", {}).catch(e => "HATA: " + e.message);
  if (out.mode === "github") {
    out.webhook = await tg(env, "setWebhook", {
      url: `${url.origin}/telegram`, secret_token: env.WEBHOOK_SECRET,
      allowed_updates: ["message", "callback_query"],
    }).catch(e => "HATA: " + e.message);
    const r = await gh(env, "GET", "");
    out.github_repo = r.ok ? "erişim tamam" : `HATA ${r.status}`;
    out.kv = env.KV ? "bağlı" : "yok (isteğe bağlı)";
  } else {
    out.webhook = await tg(env, "deleteWebhook", {}).catch(e => "HATA: " + e.message);
  }
  out.commands = await tg(env, "setMyCommands", { commands: COMMANDS }).catch(e => "HATA: " + e.message);
  return new Response(JSON.stringify(out, null, 2), { headers: { "content-type": "application/json; charset=utf-8" } });
}

// --------------------------------------------------------------------------
// Yardımcılar
// --------------------------------------------------------------------------
const text = (s, status = 200) => new Response(s, { status, headers: { "content-type": "text/plain; charset=utf-8" } });
const tgBase = env => env.TELEGRAM_API || "https://api.telegram.org";
const githubEnabled = env => !!(env.GITHUB_REPO && env.GITHUB_TOKEN);
const branch = env => env.GITHUB_BRANCH || "main";
const allowedSet = env => new Set(String(env.ALLOWED_CHAT_IDS || "").split(/[,\s]+/).filter(Boolean));

async function tg(env, method, params, form) {
  const init = form
    ? { method: "POST", body: form }
    : { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(params || {}) };
  const r = await fetch(`${tgBase(env)}/bot${env.BOT_TOKEN}/${method}`, init);
  const j = await r.json();
  if (!j.ok) throw new Error(`${method}: ${j.description}`);
  return j.result;
}

function gh(env, method, path, body, accept) {
  const api = env.GITHUB_API || "https://api.github.com";
  return fetch(`${api}/repos/${env.GITHUB_REPO}${path ? "/" + path : ""}`, {
    method,
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      "User-Agent": "yemek-bot-worker",
      Accept: accept || "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
}

async function ghList(env, dir) {
  const r = await gh(env, "GET", `contents/${dir}?ref=${branch(env)}`);
  if (r.status === 404) return [];
  if (!r.ok) throw new Error(`GitHub list ${dir}: ${r.status}`);
  const j = await r.json();
  return Array.isArray(j) ? j : [];
}

async function ghRaw(env, path) {
  const r = await gh(env, "GET", `contents/${path}?ref=${branch(env)}`, null, "application/vnd.github.raw");
  if (!r.ok) throw new Error(`GitHub raw ${path}: ${r.status}`);
  return r.arrayBuffer();
}

async function ghPutText(env, path, str, message) {
  const r = await gh(env, "PUT", `contents/${path}`, { message, content: b64(str), branch: branch(env) });
  if (!r.ok) throw new Error(`GitHub PUT ${path}: ${r.status} ${await r.text()}`);
}

async function ghDelete(env, path, sha) {
  const r = await gh(env, "DELETE", `contents/${path}`, { message: `done ${path}`, sha, branch: branch(env) });
  if (!r.ok && r.status !== 404) throw new Error(`GitHub DELETE ${path}: ${r.status}`);
}

function b64(str) {
  const bytes = new TextEncoder().encode(str);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

async function hmacHex(secret, data) {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(data));
  return [...new Uint8Array(sig)].map(b => b.toString(16).padStart(2, "0")).join("");
}
