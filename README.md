# 🍽️ Yemek Menüsü Telegram Botu (şirket PC'si + Cloudflare/GitHub aracısı)

Şirket bilgisayarı intranetteki menüyü (`erp.tai.com.tr/intranet/portal/meal/1`) okur ve
emojili bir kart görseli hazırlar. Şirket bilgisayarı Telegram'a doğrudan ulaşamadığı için
arada bir **aracı** kullanılır:

```
 MOD "relay"  (PC → *.workers.dev açıksa — önerilen, anında)
   Telegram ◀──▶ Cloudflare Worker ◀──▶ Şirket PC'si
                 (/bot<TOKEN>/... iletir)   (getUpdates / sendPhoto)

 MOD "github" (workers.dev kapalı, api.github.com açıksa)
   Telegram ──webhook──▶ Worker ──yazar──▶ GitHub repo/inbox ──okur──▶ Şirket PC'si
   Telegram ◀──gönderir── Worker ◀──push── GitHub repo/outbox+cards ◀──yazar── Şirket PC'si
```
Her iki modda da PC yalnızca dışarıya bağlantı açar. PC'de port açılmaz, dışarıdan erişim gerekmez.
GitHub modunda Telegram token'ı PC'de bile durmaz, yalnızca Worker'da bulunur.

## Komutlar
```
/yemek                 → bugün
/yemek yarın | dün | cuma | haftaya salı | 12 kasım | 12.11 | +2
/hafta                 → menüsü olan günler (butonlarla)
/yenile                → portaldan yeniden çek
```
Kartın altındaki ◀️ / ▶️ butonları aynı mesajda günler arasında gezer.

---

## 1) PC kurulumu
1. Repoyu indir (GitHub → Code → Download ZIP, veya `git clone https://github.com/ulkn/mce-yemek-bot`).
   Kalıcı bir klasöre çıkar (ör. `C:\Users\<sen>\mce-yemek-bot`) ve **`kurulum.bat`** dosyasını çalıştır.
2. **`baglanti_testi.bat`** dosyasını çalıştır. Hangi servislerin açık olduğunu gösterir ve modu önerir:
   ```
   ❌ Telegram API (doğrudan)   ERİŞİLEMİYOR
   ✅ Cloudflare Workers        erişilebilir      → "relay" kullan
   ✅ GitHub API                erişilebilir      → (relay kapalıysa) "github" kullan
   ✅ Yemek portalı             erişilebilir
   ```
3. **`giris_yap.bat`** dosyasını çalıştır. Açılan Edge penceresinde portala bir kez giriş yap.
4. **`test.bat`** dosyasını çalıştır. Bugünün kartı `card_test.jpg` olarak açılır.

## 2) Cloudflare Worker: `mce-yemek-worker`
Adres: **https://mce-yemek-worker.cahit-tr.workers.dev**. Kodu `worker/worker.js` dosyasında.
Bu adımlar şirket dışından da yapılabilir, telefon veya ev bilgisayarı yeterli.

### 2a) Kodu yayınla (iki yoldan biri)
**Otomatik (önerilen):** `worker/` klasöründe değişiklik `main` dalına gelince GitHub Actions Worker'ı yayınlar.
Bir kez şu repo secret'larını ekle (repo → Settings → Secrets and variables → Actions):
| Secret | Nereden |
|---|---|
| `CLOUDFLARE_API_TOKEN` | dash.cloudflare.com → My Profile → API Tokens → Create Token → **"Edit Cloudflare Workers"** şablonu |
| `CLOUDFLARE_ACCOUNT_ID` | Workers & Pages sayfasının sağ tarafındaki Account ID |
| `WEBHOOK_SECRET` | (isteğe bağlı) aşağıdaki Worker secret'ıyla aynı metin. Eklersen yayından sonra `/setup` da otomatik çalışır |

Sonra Actions → **"Worker'ı yayınla"** → **Run workflow**.

**Elle:** Cloudflare → Workers & Pages → `mce-yemek-worker` → Edit code → `worker/worker.js` dosyasını yapıştır → Deploy.
Elle yayınlarsan Settings bölümünde şunları da ekle:
- Variables: `GITHUB_REPO = ulkn/mce-yemek-bot`, `GITHUB_BRANCH = posta`
- Triggers → Cron: `* * * * *`

### 2b) Worker değişkenleri (Cloudflare → mce-yemek-worker → Settings → Variables and Secrets)
| Ad | Tür | Değer |
|---|---|---|
| `BOT_TOKEN` | Secret | BotFather token |
| `WEBHOOK_SECRET` | Secret | rastgele uzun metin (ör. `mce-7f3k9q2m...`) |
| `ALLOWED_CHAT_IDS` | Text | senin Telegram sohbet kimliğin (bilmiyorsan şimdilik boş bırak) |

Kontrol için şu adresleri aç:
- `https://mce-yemek-worker.cahit-tr.workers.dev/health` → `yemek-bot worker ok` görmelisin.
- `https://mce-yemek-worker.cahit-tr.workers.dev/setup?key=<WEBHOOK_SECRET>` → mod, bot adı ve webhook durumunu gösterir.

### Mod A: relay
`config.json` (`config.example.json` dosyasından kopyalanır, adres hazır):
```json
"telegram_mode": "relay",
"relay_url": "https://mce-yemek-worker.cahit-tr.workers.dev",
"telegram_token": "<BotFather token>",
"allowed_chat_ids": [123456789]
```
Bu modda Worker, Telegram'a yalnızca senin bot token'ınla gelen istekleri iletir. Başka token'larla gelen istekleri reddeder.

### Mod B: github (posta kutusu = bu repodaki `posta` dalı)
Talepler ve kartlar `ulkn/mce-yemek-bot` reposunun **`posta`** dalına yazılır
(`inbox/`, `outbox/`, `cards/`). `main` dalındaki kod etkilenmez.
1. **Fine-grained token** oluştur (GitHub → Settings → Developer settings → Fine-grained tokens):
   Repository access → yalnızca `ulkn/mce-yemek-bot`, Permissions → **Contents: Read and write**.
   Worker ve PC için ayrı iki token oluşturman önerilir.
2. Worker'a `GITHUB_TOKEN` (Secret) ekle. Worker bu secret'ı görünce kendiliğinden GitHub moduna geçer.
   (Önerilir) KV: Storage → KV → namespace oluştur. Sonra Worker → Bindings → KV, değişken adı **`KV`**.
3. `/setup?key=...` sayfasını aç. `"mode": "github"` ve `"github_repo": "erişim tamam"` görmelisin.
4. `config.json` içinde şunları ayarla (bu modda `telegram_token` gerekmez):
```json
"telegram_mode": "github",
"github": { "repo": "ulkn/mce-yemek-bot", "token": "<PC token>", "branch": "posta", "poll_seconds": 6 },
"allowed_chat_ids": [123456789]
```
Repo webhook'u (push → Worker `/github`) kurulu olursa cevaplar birkaç saniyede gelir.
Kurulu değilse Worker dakikada bir kontrol eder.

**Relay'den GitHub moduna (veya tersine) geçiş:** Worker'daki `GITHUB_TOKEN` secret'ını ekle veya sil, sonra `/setup` sayfasını yeniden aç.

## 3) Çalıştırma
- `baslat.bat`: pencereli çalıştırır. Bota `/yemek` yaz.
  Sohbet kimliğini bilmiyorsan bot ilk mesajda söyler. O sayıyı `allowed_chat_ids` alanına ve Worker'daki `ALLOWED_CHAT_IDS` alanına yaz.
- `otomatik_baslat.bat`: Windows oturumu açılınca bot arka planda başlar.
- `durdur.bat`: botu durdurur. Loglar `bot.log` dosyasına yazılır.

PC kapalı veya uykudaysa:
- **github** modunda Worker "💤 bilgisayar talepleri almıyor" uyarısı gönderir.
- İki modda da 30 dakikadan eski talepler, bilgisayar açıldığında sessizce atlanır (`max_age_minutes`).

## Olası sorunlar
| Belirti | Çözüm |
|---|---|
| Test SSL hatası veriyor | `truststore` kurulumu çoğu durumda çözer (Python 3.10+). Olmazsa şirket kök sertifikasını .pem olarak dışa aktar ve `"ca_bundle"` alanına yolunu yaz |
| Bağlantı için proxy gerekiyor | `"proxy": "http://proxy.sirket:8080"`; pip için kurulumdan önce `set HTTPS_PROXY=...` |
| `*.workers.dev` engelli ama Cloudflare'de alan adın var | Worker → Settings → Domains & Routes → kendi alt alan adını ekle (ör. `yemek.alanadin.com`) ve `relay_url` alanına onu yaz |
| "🔐 Portal oturumu düşmüş" | `giris_yap.bat` |
| relay modunda "Conflict: webhook is active" | `/setup?key=...` sayfasını aç. Relay modunda webhook'u siler |
| AKINCI yerine 3. Vardiya | `"menu_url": ".../intranet/portal/meal/2"` |

**Çevrimdışı paket kurulumu** (PyPI kapalıysa): İnternete açık bir bilgisayarda şunu çalıştır:
`pip download -r requirements.txt -d wheels --only-binary=:all: --platform win_amd64 --python-version 3.11`.
`wheels` klasörünü şirket bilgisayarına taşı, sonra:
`.venv\Scripts\pip install --no-index --find-links wheels -r requirements.txt`.

## Dosyalar
| Dosya | Görev |
|---|---|
| `bot.py` | Komutlar, tarih yorumlama, butonlar, ana döngü |
| `transports.py` | Telegram'a ulaşma: relay / github / direct |
| `menu.py` | Portalı Edge ile okuma, ayrıştırma, önbellek |
| `card.py` | Emojili kart tasarımı (HTML → JPEG) |
| `baglanti_testi.py` | Hangi servislere erişildiğini test eder |
| `worker/worker.js` | Cloudflare Worker (relay + GitHub posta kutusu) |
| `worker/wrangler.toml` | Worker'ı komut satırından yayınlamak isteyenler için |
