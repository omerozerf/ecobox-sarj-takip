# ecobox-sarj-takip

Ecobox AC şarj prizlerinin durumunu gece gündüz kaydeden küçük bir Python servisi ve üstünde
çalışan bir Telegram botu ile bir Slack botu. Bot Hacettepe Teknokent'i gösterir; kayıt bütün
Ecobox sitelerini kapsar.

- **Veri havuzu:** her 3 dakikada bir tüm Ecobox prizlerinin durumu SQLite'a yazılır (tek durum
  isteği zaten hepsini getirir). Bot komutları ve bildirimler yalnızca görünen kapsamı
  (`ECOBOX_SCOPE`, varsayılan Hacettepe) gösterir; diğer siteler karşılaştırma için tutulur.
- **Analiz:** biriken geçmiş, yüksek sesle söylenebilecek cümlelere çevrilir: hangi saatte boş
  priz bulmak kolay, insanlar ne zaman takıp çıkarıyor, bir araç prizde ne kadar kalıyor,
  hangi priz yürümeye değer, hangisi arızalı, Hacettepe diğer sitelere göre ne kadar yoğun, bu
  hafta geçen haftadan farklı mı. Her bulgu neye dayandığını yazar; verisi yetmeyen bulgu tahmin
  etmez, neye ihtiyacı olduğunu tek satırda söyler.
- **Grafikler:** telefonda okunur PNG'ler (gün × saat ısı haritası, takma/çıkarma saatleri,
  priz zaman çizelgesi, prizde kalma süresi, site karşılaştırması); başlıkları çıkarımı söyler.
- **Telegram ve Slack botu:** anlık durum, "boşalınca haber ver", istatistik, analiz,
  grafik, haftalık özet ve şarj süresi/ücret tahmini. İkisi aynı komutları bilir; biri, ikisi
  ya da hiçbiri açık olabilir.

Kaydedici ve metin komutları yalnızca Python standart kütüphanesini kullanır (Python 3.14).
Grafikler için matplotlib gerekir; proje içindeki `.venv`'e kurulur. matplotlib yoksa her şey
çalışmaya devam eder, yalnızca grafik istenince bunu söyleyen bir cevap gelir. Telegram ya da
Slack token'ı olmadan da kaydedici çalışır; veri toplamaya bot hazır olmadan başlanabilir.

**Süre adı:** API bir prizin "dolu" olduğunu söyler ama araç hâlâ şarj mı oluyor, yoksa şarjı
bitip takılı mı duruyor ayırt etmez; enerji ya da araç bilgisi de vermez. Bu yüzden bir aracın
süresi her yerde **prizde kalma süresi** diye geçer, şarj süresi değil.

## Başlangıç

```bash
cd ecobox-sarj-takip
cp .env.example .env            # gerekirse düzenleyin
scripts/setup_venv.sh           # .venv + matplotlib (grafikler için; isteğe bağlı)
python3 -m ecobox --help
python3 -m ecobox poll-once     # ilk canlı kontrol: listeyi ve durumu bir kez çeker, yazdırır
python3 -m ecobox run           # sürekli çalıştır (Ctrl+C ile durur)
```

Diğer komutlar (ağ isteği yapmaz, yalnızca veritabanını okur):

```bash
python3 -m ecobox status                    # son kaydedilen durum
python3 -m ecobox status --location ego     # yalnızca bir konum
python3 -m ecobox stats                     # görünen kapsam (Hacettepe)
python3 -m ecobox stats --location ego      # konum filtresi (ör. ego, "f blok", arge)
python3 -m ecobox estimate 20 80            # %20 → %80 süre, bitiş saati, ücret
python3 -m ecobox insights                  # botun `analiz` cevabı
python3 -m ecobox insights --location ego   # konum; kapsam dışı bir site de olabilir
.venv/bin/python -m ecobox charts --out /tmp/grafikler            # bütün grafikler
.venv/bin/python -m ecobox charts --out /tmp/grafikler --kind saat
python3 -m ecobox poll-once --no-store      # veritabanına yazmadan bir kez çek
python3 -m ecobox import-snapshots          # GitHub görüntülerini Mac'in boşluklarına ekle
```

Testler ağa çıkmaz; fixture, sentetik geçmiş ve sahte istemcilerle çalışır:

```bash
.venv/bin/python -m unittest discover -s tests          # hepsi
/opt/homebrew/bin/python3 -m unittest discover -s tests # matplotlib'siz; çizim testleri atlanır
```

## Arka planda sürekli çalıştırma (launchd)

```bash
scripts/install_launchd.sh      # ~/Library/LaunchAgents/local.ecobox-sarj-takip.plist kurar ve başlatır
scripts/uninstall_launchd.sh    # durdurur ve kaldırır (veri ve loglar kalır)
```

Servis oturum açıldığında başlar ve çökerse yeniden başlatılır. Loglar `logs/` altında.
`.venv` varsa servis onun Python'uyla çalışır (grafikler için), yoksa
`/opt/homebrew/bin/python3` ile. `.venv`'i sonradan kurduysanız `install_launchd.sh`'ı yeniden
çalıştırın; servis kodu yalnızca açılışta yüklediği için kod değişiklikleri de yeniden
başlatınca devreye girer.
Mac uyurken kayıt olmaz; bu boşluklar veritabanında işaretlenir ve zamanlama istatistiklerine
katılmaz (aşağıya bakın). Boşlukları GitHub'daki ikinci kaydedici doldurabilir (sonraki bölüm).

## Mac uyurken: GitHub görüntüleri

GitHub Actions'taki `collect` iş akışı (`.github/workflows/collect.yml`) yaklaşık 5 dakikada bir
bütün prizlerin ham durumunu çekip deponun `data` dalına `snapshots/` altına yazar (biçim:
`ecobox/snapshot.py`). GitHub zamanlanmış işleri sık sık geciktirir ya da atlar; bu yüzden bu
kaynak Mac'in yerini tutmaz, yalnızca boşluklarını doldurur.

`.env`'de `ECOBOX_SNAPSHOT_REPO_URL` doluysa servis açılışta ve sonra saatte bir
(`ECOBOX_SNAPSHOT_IMPORT_S`) `data` dalını `data/remote` altına girişsiz https ile çeker (sistemin
`git`'i; ilk seferde sığ klon, sonra yalnızca yenileri) ve görüntüleri veritabanına ekler. Bu iş
kendi iş parçacığında çalışır; git ya da uzun bir geriye dönük ekleme kaydediciyi bekletmez.
Aynısı elle: `python3 -m ecobox import-snapshots` (ya da `--dir <klasör>` ile git'siz). Adres
boşsa özellik kapalıdır.

Kural: bir görüntü yalnızca Mac'in kendisinin görmediği bir ana aitse kullanılır. Mac'in başarılı
bir okuması görüntüye yarım aralıktan (varsayılan 90 sn) yakınsa ya da görüntü, aralarında boşluk
eşiğinden (7,5 dk) az olan iki Mac okumasının arasındaysa görüntü atlanır. Yani Mac uyanıkken
yalnızca Mac'in örnekleri kalır; iki kaynak birbirine karışmaz. Yarım aralık, bir boşluğun
kenarında Mac'in son okumasıyla ilk görüntü arasındaki adımı eşiğin altında tutar (90 + 300 sn
< 450 sn), yani doldurmanın kendisi kenarda yapay bir boşluk yaratmaz.

- Eklenen örnekler `poll_log`'da `kind='snapshot'` satırlarına bağlıdır (Mac'inkiler
  `kind='status'`); kapsam kaynağa göre ayrı raporlanır.
- Geçmişe örnek eklenince o prizin geçişleri ve boşlukları, eklenen ilk örnekten önceki örnekten
  eklenen son örnekten sonraki örneğe kadar kaydedicinin kendi kuralıyla yeniden hesaplanır
  (`ecobox/sequence.py`): sonuç, kaydedici birleşik diziyi sırayla görmüş gibidir. Görüntüler
  arasında da eşikten uzun ara varsa (GitHub bir işi atladıysa) boşluk yazılır ve geçiş
  `uncertain` işaretlenir.
- Bir görüntü en az boşluk eşiği kadar (7,5 dk) eskiyince alınır; daha yenisi bir sonraki
  çalıştırmaya kalır, çünkü Mac'in yanına düşecek bir okuması henüz gelebilir.
- İlerleme `service_state`'te (`snapshot_import_cursor`) tutulur; her çalıştırma yalnızca
  yenileri okur. Aynı görüntü iki kez eklenmez (ilerleme kaydı silinse bile).
- Yerel katalogda olmayan prizlerin satırları atlanır, kaydedicinin kayıt kapsamı dışındaki
  prizleri atlaması gibi; katalog boşsa hiçbir şey alınmaz ve görüntüler sonraya kalır.

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

Komutlar: `/durum [konum]`, `/haberver [konum]`, `/iptal`, `/istatistik [konum]`,
`/analiz [konum]`, `/grafik [tür] [konum]`, `/tahmin <başlangıç %> <hedef %> [konum]`, `/yardim`.
Bot açılınca bu komutları Telegram'ın "/" menüsüne de yazar (`setMyCommands`; olmazsa bot yine
çalışır). Komut yazmadan bir konum adı yazmak (`ego`, `f blok boş mu`) `durum <konum>` sayılır;
tanınmayan başka bir mesaja kısa bir "tanımıyorum" cevabı gelir. Bilinmeyen bir konuma cevap,
yazılabilecek kısa konum adlarını önerir (ör. `arge · ego · f blok · otopark`).

- `durum [konum]`: şu an hangi priz boş. Hiç boş priz yoksa ve geçmişte en az 3 gün bu saatte
  ölçülebildiyse "Bu saatte genelde … içinde biri boşalıyor" satırı eklenir.
- `istatistik [konum]`: şimdi gidersem boş bulur muyum. Önümüzdeki saatlerin boş priz şansı
  (kayıt olmayan saatler tek satırda), takma ve çıkarmanın en yoğun 2 saati (`analiz` ile aynı
  hesap ve eşik), prizde kalma süresi ve en sık boş prizler. Hepsi doluysa bekleme bilgisi başta.
- `analiz [konum]`: geçmiş: ne zaman boş, araçlar kaç saat kalıyor (aşağıda).

`/haberver` bir kez çalışır: eşleşen bir AC priz boş duruma geçince mesaj atar ve abonelik kapanır.
Bildirim kurulduğu konumu korur ("Tekrar haber için: /haberver ego").

## Slack botunu kurma

Bot, Slack'te yalnızca sizin ve botun bulunduğu özel bir kanalda çalışır. Herkese açık bir adres
ya da websocket gerekmez: servis birkaç saniyede bir kanalın geçmişini Slack Web API'den okur
(`conversations.history`) ve cevabı `chat.postMessage` ile yazar.

1. https://api.slack.com/apps adresinde **Create New App → From scratch** ile çalışma alanınıza
   bir uygulama açın.
2. **OAuth & Permissions → Bot Token Scopes** altına şu beş kapsamı ekleyin:
   `chat:write`, `groups:write`, `groups:read`, `groups:history` ve grafikleri yüklemek için
   `files:write`. Bot zaten kuruluysa `files:write`'ı ekleyip uygulamayı çalışma alanına
   yeniden kurun (**Reinstall to Workspace**); token değişmez. İzin yoksa bot grafiğin yerine
   bunu söyleyen bir mesaj yazar, diğer komutlar çalışmaya devam eder.
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
`istatistik f blok`, `analiz`, `grafik saat ego`, `tahmin 20 80`, `yardim` (ya da `yardım`). Büyük/küçük harf fark etmez;
başına `!` koyarsanız da çalışır. Kanalda yalnızca `SLACK_USER_ID`'nin yazdıkları cevaplanır.

Abonelikler yapıldıkları yere bağlıdır: Slack'te `haberver` derseniz bildirim Slack'e,
Telegram'da `/haberver` derseniz Telegram'a gelir; ikisi aynı anda açık olabilir. `iptal`
yalnızca yazıldığı taraftaki aboneliği kapatır.

Grafik istenince ayrı bir metin mesajı gelmez: resim, altında çıkarımı ve diğer grafik türleri
yazan kısa bir altyazıyla gider (Slack'te dosya başlığı ve dosyayla gelen yorum).

Slack'in hız sınırına takılırsa (HTTP 429) servis Slack'in söylediği süre kadar bekler. Slack ya
da Telegram tarafındaki bir hata kaydediciyi ve diğer botu durdurmaz. Token loglara yazılmaz.

## Analiz ve grafikler

`analiz [konum]` bulguları en kullanışlıdan başlayarak sıralar; her bulgunun altında `↳` ile
neye dayandığı yazar. Verisi yetmeyenler en altta tek bir `⏳` paragrafında, bekledikleri veriye
göre gruplanır ("5 araç dolunca (şu an 3): prizde kalma süreleri. 1 günlük kayıt dolunca: …").
Saat bulgusu 3 günden az kayıtta genel bir kalıp gibi değil "Şimdiye kadar (N gün)" diye yazılır.
Sözcükler sabittir: prizdeki bir kullanım "araç", olaylar "takma / çıkarma", veri kapsamı
"N saatlik/günlük kayıt".
Konum verilmezse görünen kapsam (Hacettepe) analiz edilir; görünen kapsamda eşleşmeyen bir
konum bütün kayıtlı sitelerde aranır. Analiz son 8 haftaya bakar, veri biriktikçe kendiliğinden
netleşir.

| Bulgu | Gerekli en az veri |
|---|---|
| Boş priz bulmanın en kolay/zor saati (hafta içi ve hafta sonu ayrı) | 3 saat diliminde 10'ar okuma (yarım saat) |
| Saat bulgusunun "hafta içi" kalıbı olarak yazılması | O gün grubunda 3 farklı tarih |
| İlk bakılacak priz / nadiren boş olanlar (zamanın %10'undan az) | Priz başına 60 okuma (3 saat) |
| Prizde kalma süresi | 5 araç (kayıt kesintisine denk gelmeyen) |
| Takma ve çıkarma saatleri | 5 kesin takma ya da çıkarma |
| Geliş saatine göre kalma süresi | 2 zaman diliminde 3'er araç |
| Priz başına günlük kullanım | Priz başına 1 günlük kayıt |
| 4 saatten uzun kalanlar | 5 araç |
| Arıza, çevrimdışı, bakım payı | Priz başına 60 okuma |
| Diğer Ecobox siteleriyle karşılaştırma | Ortak pencerede en az 3 başka site ve 1 günlük kayıt |
| Geçen haftaya göre değişim | Son iki haftanın her birinde 2,5 günlük kayıt |

Eşikler `ecobox/insights.py` içindeki `Thresholds`'ta; yeni bir bulgu türü bir kural
fonksiyonu yazıp `DEFAULT_RULES`'a eklenerek gelir.

`grafik [tür] [konum]` türleri: `harita` (varsayılan; gün × saat boş priz şansı; eski adı `isi`
de çalışır), `saat` (takma ve çıkarma saatleri), `zaman` (priz zaman çizelgesi, son 24 saat ve
son 7 gün; 7 günlük resim 1 gün kayıttan sonra gelir), `sure` (prizde kalma süresi), `site`
(sitelere göre AC doluluk; yalnızca en az 2 AC prizli siteler, sıralamada Hacettepe'nin 4 üstü
ve 4 altı, en altta diğer sitelerin ortalaması; `analiz` ile aynı eşikleri bekler). Grafik
başlığı çıkarımın kendisidir ("Hafta içi boş priz en kolay 07:00–08:00"); eşik dolmadıysa tarif
edici bir başlık kalır. Kayıt olmayan hücreler taralı çizilir, "%0"dan ayırt edilir; her grafik
alt başlığında kaç prize ve ne kadar kayda dayandığını yazar. Yazılar telefonda (≈360 px
genişlik) okunacak boyutta: hiçbiri 15 punto altında değil.

**Haftalık özet:** `DIGEST_WEEKDAY` ve `DIGEST_HOUR` (İstanbul saati) geldiğinde, son 7 günün
bulguları (önce haftalık değişim, en fazla 4 bulgu, "henüz erken" listesi olmadan) ve aynı 7
günün gün × saat grafiği açık olan her bota kendiliğinden gider. Mac o an uykudaysa aynı hafta
uyanınca gönderilir; haftada bir kereden fazla gitmez (gönderilen hafta `service_state`'te
tutulur). Toplam kayıt `DIGEST_MIN_DAYS` günden azsa gönderilmez. `DIGEST_WEEKDAY=0` kapatır.

## Veriler nerede

`data/ecobox.sqlite3` (git'e girmez). Tüm zamanlar UTC saklanır; saat/gün istatistikleri
İstanbul saatine göre hesaplanır.

| Tablo | İçerik |
|---|---|
| `poll_log` | Her istek (durum ve istasyon listesi), başarılı/hatalı, süre; GitHub'dan alınan görüntüler `kind='snapshot'`. Kapsama buradan görülür. |
| `charge_points`, `connectors` | Kaydedilen istasyonlar ve prizler (tip, güç, ham JSON, çalışma saati ve otomatik şarj bayrakları, son rezervasyon alanları). |
| `catalog_snapshots` | Her istasyon listesi yenilemesinde her prizin rezervasyon, bakım, çalışma saati ve erişim alanları; değişimleri geriye dönük görmek için. |
| `tariff_history` | Her prizin tarife geçmişi; fiyat değişince yeni satır. |
| `samples` | Her okumada her prizin normalize durumu ve ham alanları; API yeni bir alan göndermeye başlarsa `extra_json`'da saklanır. |
| `transitions` | Durum değişimleri; `uncertain=1` ise gerçek zaman bilinmiyor. |
| `gaps` | Kayıt boşlukları (önceki örnek 2,5 × aralıktan eski). |
| `subscriptions` | Bekleyen "haber ver" abonelikleri; `transport` (telegram/slack) hangi tarafta bildirileceğini söyler. |
| `service_state` | Kalıcı servis durumu: Slack kanal id'si, işlenen son Slack mesajının zamanı, GitHub görüntülerinde gelinen yer. |

Şema değişiklikleri servis açılırken kendiliğinden uygulanır; yalnızca ekleme yapar (eksik tablo
ve sütunları ekler), mevcut satırlara dokunmaz ve tekrar çalışınca bir şey değiştirmez.

Rezervasyon alanları (`connectorReservationStatus`, `nearByConnectorReservations`) durum
isteğinde değil istasyon listesinde geliyor; liste günde bir kez çekildiği için bu alanların
geçmişi günlük çözünürlüktedir.

**Disk:** bütün siteler kaydedilince veritabanı günde yaklaşık 18 MB büyür (168 priz × 480
okuma; sentetik bir günle ölçüldü), ayda ~0,5 GB. Yalnızca Hacettepe kaydetmek için
`ECOBOX_RECORD_SCOPE=hacettepe`.

**Boşluklar:** Bir prizin önceki örneği 2,5 × okuma aralığından (varsayılan 7,5 dk) eskiyse, o
arada olan değişimin zamanı bilinmez. Bu geçişler `uncertain` işaretlenir; takılma/çıkarma saati
istatistiklerine katılmaz. İçinde boşluk olan ya da belirsiz bir geçişle başlayan/biten
oturumlar prizde kalma süresi istatistiğine katılmaz ("kayıt kesintisine denk gelen N araç
sayılmadı"). Doluluk oranları yalnızca gerçekten gözlenen okumaları
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
