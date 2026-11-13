# ecobox-sarj-takip

Hacettepe Teknokent'teki Ecobox AC şarj prizlerinin durumunu gece gündüz kaydeden küçük bir
Python servisi ve üstünde çalışan bir Telegram botu ile bir Slack botu.

- **Veri havuzu:** her 3 dakikada bir tüm prizlerin durumu SQLite'a yazılır. Birkaç hafta sonra
  şu sorular cevaplanabilir: insanlar ne zaman takıp ne zaman çıkarıyor, oturumlar ne kadar
  sürüyor, hangi gün/saatte boş priz bulma ihtimali yüksek, hangi priz en çok boş.
- **Telegram ve Slack botu:** anlık durum, "boşalınca haber ver", istatistik ve şarj
  süresi/ücret tahmini. İkisi aynı komutları bilir; biri, ikisi ya da hiçbiri açık olabilir.

Yalnızca Python standart kütüphanesi kullanılır (Python 3.14, `/opt/homebrew/bin/python3`).
Telegram ya da Slack token'ı olmadan da kaydedici çalışır; veri toplamaya bot hazır olmadan
başlanabilir.

## Başlangıç

```bash
cd ecobox-sarj-takip
cp .env.example .env            # gerekirse düzenleyin
python3 -m ecobox --help
python3 -m ecobox poll-once     # ilk canlı kontrol: listeyi ve durumu bir kez çeker, yazdırır
python3 -m ecobox run           # sürekli çalıştır (Ctrl+C ile durur)
```

Diğer komutlar (ağ isteği yapmaz, yalnızca veritabanını okur):

```bash
python3 -m ecobox status                    # son kaydedilen durum
python3 -m ecobox stats                     # tüm AC prizler
python3 -m ecobox stats --location ego      # konum filtresi (ör. ego, "f blok", arge)
python3 -m ecobox estimate 20 80            # %20 → %80 süre, bitiş saati, ücret
python3 -m ecobox poll-once --no-store      # veritabanına yazmadan bir kez çek
```

Testler: `python3 -m unittest discover -s tests` (ağa çıkmaz; fixture ve sahte istemcilerle çalışır).

## Arka planda sürekli çalıştırma (launchd)

```bash
scripts/install_launchd.sh      # ~/Library/LaunchAgents/local.ecobox-sarj-takip.plist kurar ve başlatır
scripts/uninstall_launchd.sh    # durdurur ve kaldırır (veri ve loglar kalır)
```

Servis oturum açıldığında başlar ve çökerse yeniden başlatılır. Loglar `logs/` altında.
Mac uyurken kayıt olmaz; bu boşluklar veritabanında işaretlenir ve zamanlama istatistiklerine
katılmaz (aşağıya bakın).

## Telegram botunu kurma

1. Telegram'da **@BotFather**'a yazın, `/newbot` deyin, bir isim ve `_bot` ile biten bir
   kullanıcı adı verin. Size bir token verir.
2. Token'ı `.env` içine yazın: `TELEGRAM_BOT_TOKEN=123456:ABC...`
   `TELEGRAM_CHAT_ID` şimdilik boş kalsın.
3. Servisi başlatın (`python3 -m ecobox run` ya da launchd ile) ve bota herhangi bir mesaj atın.
   Bot size sohbetin chat id'sini söyler.
4. Bu sayıyı `.env` içine `TELEGRAM_CHAT_ID=...` olarak yazın ve servisi yeniden başlatın
   (launchd'de: `scripts/install_launchd.sh` yeniden çalıştırılabilir).

Bot yalnızca bu chat id'ye hizmet verir; başka sohbetlere "özel kullanım" der. Token loglara
yazılmaz.

Komutlar: `/durum`, `/haberver [konum]`, `/iptal`, `/istatistik [konum]`,
`/tahmin <başlangıç %> <hedef %> [konum]`, `/yardim`.
`/haberver` bir kez çalışır: eşleşen bir AC priz boş duruma geçince mesaj atar ve abonelik kapanır.

## Slack botunu kurma

Bot, Slack'te yalnızca sizin ve botun bulunduğu özel bir kanalda çalışır. Herkese açık bir adres
ya da websocket gerekmez: servis birkaç saniyede bir kanalın geçmişini Slack Web API'den okur
(`conversations.history`) ve cevabı `chat.postMessage` ile yazar.

1. https://api.slack.com/apps adresinde **Create New App → From scratch** ile çalışma alanınıza
   bir uygulama açın.
2. **OAuth & Permissions → Bot Token Scopes** altına şu dört kapsamı ekleyin:
   `chat:write`, `groups:write`, `groups:read`, `groups:history`.
3. **Install to Workspace** deyin ve verilen **Bot User OAuth Token**'ı (`xoxb-...`) `.env` içine
   `SLACK_BOT_TOKEN=...` olarak yazın.
4. Kendi üye id'nizi bulun (Slack'te profiliniz → ⋮ → **Copy member ID**, `U...` ile başlar) ve
   `SLACK_USER_ID=...` olarak yazın. Kanal adı `SLACK_CHANNEL_NAME` (varsayılan `ecobox-sarj`).
5. Servisi yeniden başlatın. İlk açılışta bot özel kanalı kendisi açar, sizi davet eder ve
   yardım mesajını yazar. Elle bir şey yapmanız gerekmez.

Kanal id'si ve işlenen son mesajın zamanı veritabanında (`service_state` tablosu) tutulur;
yeniden başlatmada kanal tekrar açılmaz, eski mesajlara tekrar cevap verilmez. Kanal adı
Slack'te zaten kullanılıyorsa bot başka bir ad uydurmaz: logda bunu yazar ve yalnızca Slack
tarafı durur. `.env`'de kullanılmayan bir ad seçip servisi yeniden başlatın.

**Slack'te komutlar başına `/` koymadan yazılır**, çünkü Slack `/` ile başlayan her mesajı kendi
slash komutu sanıp yutar: `durum`, `haberver ego`, `haberver f blok`, `iptal`,
`istatistik f blok`, `tahmin 20 80`, `yardim` (ya da `yardım`). Büyük/küçük harf fark etmez;
başına `!` koyarsanız da çalışır. Kanalda yalnızca `SLACK_USER_ID`'nin yazdıkları cevaplanır.

Abonelikler yapıldıkları yere bağlıdır: Slack'te `haberver` derseniz bildirim Slack'e,
Telegram'da `/haberver` derseniz Telegram'a gelir; ikisi aynı anda açık olabilir. `iptal`
yalnızca yazıldığı taraftaki aboneliği kapatır.

Slack'in hız sınırına takılırsa (HTTP 429) servis Slack'in söylediği süre kadar bekler. Slack ya
da Telegram tarafındaki bir hata kaydediciyi ve diğer botu durdurmaz. Token loglara yazılmaz.

## Veriler nerede

`data/ecobox.sqlite3` (git'e girmez). Tüm zamanlar UTC saklanır; saat/gün istatistikleri
İstanbul saatine göre hesaplanır.

| Tablo | İçerik |
|---|---|
| `poll_log` | Her istek (durum ve istasyon listesi), başarılı/hatalı, süre. Kapsama buradan görülür. |
| `charge_points`, `connectors` | Kapsamdaki istasyonlar ve prizler (tip, güç, ham JSON). |
| `tariff_history` | Her prizin tarife geçmişi; fiyat değişince yeni satır. |
| `samples` | Her okumada her prizin normalize durumu ve ham alanları. |
| `transitions` | Durum değişimleri; `uncertain=1` ise gerçek zaman bilinmiyor. |
| `gaps` | Kayıt boşlukları (önceki örnek 2,5 × aralıktan eski). |
| `subscriptions` | Bekleyen "haber ver" abonelikleri; `transport` (telegram/slack) hangi tarafta bildirileceğini söyler. |
| `service_state` | Botların kalıcı durumu: Slack kanal id'si, işlenen son Slack mesajının zamanı. |

Şema değişiklikleri servis açılırken kendiliğinden uygulanır; yalnızca ekleme yapar (eksik tablo
ve sütunları ekler), mevcut satırlara dokunmaz ve tekrar çalışınca bir şey değiştirmez.

**Boşluklar:** Bir prizin önceki örneği 2,5 × okuma aralığından (varsayılan 7,5 dk) eskiyse, o
arada olan değişimin zamanı bilinmez. Bu geçişler `uncertain` işaretlenir; takılma/çıkarma saati
istatistiklerine katılmaz. İçinde boşluk olan ya da belirsiz bir geçişle başlayan/biten şarj
oturumları süre istatistiğine katılmaz. Doluluk oranları yalnızca gerçekten gözlenen okumaları
sayar. Her istatistik kaç gözleme dayandığını yazar.

## Uyarı: resmi olmayan uç nokta

Veri, Ecobox'ın kendi web haritasının (harita.ecoboxsarj.com) kullandığı, girişsiz ama
**belgelenmemiş** bir API'den gelir. Ecobox bunu her an değiştirebilir ya da engelleyebilir;
kullanım koşulları bu tür otomatik okumayı açıkça düzenlemiyor. Bu yüzden servis nazik çalışır:
okuma başına tek durum isteği, varsayılan 180 sn aralık, istasyon listesi günde bir kez, zaman
aşımı ve hata durumunda 15 dakikaya kadar katlanan bekleme, kişisel bilgi içermeyen genel bir
User-Agent. Aralığı kısaltmayın.

## Şarj tahmini

Araç bu AC prizlerde 10,6 kW çeker ve batarya yüzdesi başına istasyondan ~0,95 kWh alır
(≈ saatte %11). İkisi de `.env`'de değiştirilebilir. Ücret, prizin güncel tarifesindeki
`energyPrice` ile hesaplanır ve KDV dahil kabul edilir (`PRICE_INCLUDES_VAT`). Tarifede ayrıca
bir `servicePrice` alanı var; birimi API'de belirtilmediği için hesaba katılmaz, cevapta ayrıca
gösterilir.
