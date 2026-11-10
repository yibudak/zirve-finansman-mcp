# Zirve Finansman veritabanı notları

Zirve Finansman'ın veritabanı şeması için resmî bir doküman yayımlanmamıştır. Bu notlar çalışan bir
kurulumun (Finansman .Net SQL V9, SQL Server 2014 Express) salt-okunur incelenmesiyle çıkarılmıştır.
**[doğrulandı]** işaretli bilgiler Zirve'nin kendi tablo/prosedürlerinden, **[çıkarım]** işaretliler
veri dağılımından gelir.

## Kaynaklar

- Kurulum klasöründeki (`C:\zirvenet`) `VeriTabaniEsitle.xml`: tüm tablo, view, prosedür ve
  fonksiyonların `CREATE` betikleri. Dört bölümü vardır: `ZirveGenel`, `FirmaGenel`, `FirmaYil`,
  `FirmaYilTicari`. Şema için en güvenilir kaynak budur.
- `Yardim\` klasörü: kullanım kılavuzları (PDF, CHM). Ekran kullanımını anlatır, şemayı anlatmaz.
- Zirve Sunucu servisi 9566 portunda bir REST API sunar (`/swagger/index.html`; "Müşavir Konnektör":
  `GET /v1/musavir/fis`, `GET /v1/musavir/hesap-plani` …). Giriş ayrı bir **Connector API lisansı**
  ister; lisans yoksa `code 76` hatası döner. Bu proje o API'yi kullanmaz.

## Veritabanı düzeni

SQL Server instance'ı genellikle `ZRVSQL2014` adındadır, karma kimlik doğrulama ve `Turkish_CI_AS`
collation kullanır.

| Veritabanı | İçerik |
|---|---|
| `zirvegenel` | Tüm firmalar için ortak tanımlar: firma listesi (`FirmalarListesi`), döviz cinsleri (`dovizcin`), kurlar (`dovizkur`), vergi daireleri, şablon hesap planı |
| `{FIRMA}_GENEL` | Firmanın yıldan bağımsız verisi: bordro/personel, beyannameler, demirbaşlar |
| `{FIRMA}_{YIL}` | **Genel muhasebe**: hesap planı, fişler, yevmiye, e-Defter |
| `{FIRMA}_{YIL}T` | Ticari modül: cari, stok, fatura, kasa, banka, çek/senet |

`{FIRMA}`, giriş ekranındaki "Firma Tanıtıcı Adı"dır ve Türkçe karakter içerebilir. Muhasebe
veritabanında `dbo` dışında `EBelge`, `hub`, `ExpressAktarim`, `Okc`, `eEnvanter`, `ZReport` şemaları
da bulunur.

Yıl veritabanları arasında küçük şema farkları vardır (yeni sürümler eski yıllara her kolonu eklemez).
Aşağıdaki çekirdek kolonlar incelenen tüm yıllarda ortaktır.

## Genel muhasebe tabloları (`{FIRMA}_{YIL}`)

### `hesplan` — hesap planı
Anahtar: `Kod`.

| Kolon | Anlam |
|---|---|
| `Kod` | Hesap kodu. Ayırıcı çoğunlukla `.`; `,` ve `-` içeren kodlar da olabilir |
| `Aciklama` | Hesap adı |
| `Detay` | `E` = kayıt girilebilen alt hesap, `H` = ana/grup hesap |
| `Vergikimlikno`, `Tckimlikno`, `Vergidairesi`, `Ulkekodu` | Cari bilgisi (çoğu kurulumda seyrek doludur) |
| `Topb`, `Topa` | Önbelleklenmiş borç/alacak toplamı. Güvenilmez; toplamları `yevmiye`den hesaplayın |

### `fisbil` — fiş başlığı
Anahtar: `Refno`.

| Kolon | Anlam |
|---|---|
| `Fistar` | Fiş tarihi |
| `Fistur` | Fiş türü: `0` açılış, `1` tahsil, `2` tediye, `3` mahsup, `9` kapanış **[doğrulandı: yardım dosyası]**; `4` yansıtma, `5` enflasyon düzeltmesi **[çıkarım: fiş açıklamalarından]** |
| `Fisno` | 9 haneli, sıfır dolgulu fiş numarası. Tekil olması garanti değildir |
| `Yevno` | Yevmiye numarası (numaralandırma yapılana kadar boş) |
| `Btop`, `Atop` | Borç / alacak toplamı |
| `Nereden` | Kaynağı; dışarıdan/entegrasyonla gelen fişlerde `G` |
| `Ozkod` | Fişi kaydeden Zirve kullanıcısı |
| `Ekaciklama` | Fiş açıklaması |
| `Kayittarihi` | Kayıt zamanı |

Silinen fişler `fissil` / `yevmisil` tablolarına taşınır; değişiklik günlüğü `LogFis` / `LogYev`dedir.
Bir fiş düzeltildiğinde eski `Refno` `fisbil`den kalkar ve fiş yeni bir `Refno` alır (`LogFis.LogTuru = 'D'`,
yeni değer `LogFis.YeniRef`); bu yüzden `Refno` kalıcı bir dış anahtar olarak kullanılmamalıdır.

### `yevmiye` — fiş satırları
Anahtar: `Refno` (fiş) + `Refno2` (satır sırası).

| Kolon | Anlam |
|---|---|
| `Gmkod` | Hesap kodu (`hesplan.Kod`) |
| `Tip` | `B` borç, `C` alacak |
| `Borclu`, `Alacakli` | TL tutar; karşı taraf `NULL`dır. `float` tutulur, yuvarlayın |
| `Dovizadi` | Döviz cinsi kodu → `zirvegenel.dbo.dovizcin.Kod` (varsayılan: 1 TL, 2 USD, 3 EUR); boş = TL **[doğrulandı]** |
| `Doviz` | Döviz tutarı |
| `Fistar`, `Fistur`, `Fisno`, `Yevno` | Başlıktan kopyalanır |
| `Aciklama` | Satır açıklaması |
| `Evraktarihi`, `Evrakno`, `serino` | Belge tarihi / numarası (fatura no vb.) |
| `Vergino` | Karşı tarafın VKN/TCKN'si |
| `Belge_Turu` | e-Defter belge türü: `1` fatura, `8` diğer (adı `Belge_Turu_Aciklamasi`nda; ör. banka ekstresi, bordro), `9` belge yok **[çıkarım: veriden]**; `2`, `3`, `6` da görülür, anlamları doğrulanmadı (muhtemelen çek, senet, makbuz) |
| `Belge_Turu_Aciklamasi` | "Diğer" belge türünün serbest metin adı |
| `Odeme_Sekli` | e-Defter ödeme şekli. Doğrulanmadı; kullanıldığı hesap gruplarına göre muhtemelen `1` kasa, `2` banka, `3` çek, `4` senet, `5` kredi kartı |
| `Vade_Tarihi`, `Masrafmerkezi`, `Miktar`, `Stk` | Vade, masraf merkezi, miktar, stok kodu |
| `Islemtipi` | Zirve'nin muavin prosedürü (`spMuavin`) yalnızca `1` ve `3` değerlerini sayar **[doğrulandı]** |

İndeksler: `yevmiye(Refno, Refno2)` kümelenmiş, `yevmiye(Gmkod)`, `fisbil(Refno)`, `hesplan(Kod)`.

### Diğer
- `ISLEMKOD`: ticari modül işlem kodları (30–39 cari, 40–48 kasa, 50–59 banka, 60–69 fatura, …).
- `tbMuhasebelestirmeBelgeTuru`: muhasebeleştirme belge türleri (60 satış faturası, 62 alış faturası, …).
- `EBelge.tbEBelge`, `hub.tbInvoiceHead`: indirilen e-belgeler.

## Raporları yeniden üretmek

Zirve'nin `spMuavin` prosedürü geçici tablo ve cursor kullanır; çağırmak yerine aynı sonucu düz
`SELECT` ile üretin:

- **Mizan**: `yevmiye` üzerinde `Gmkod`a göre `SUM(Borclu)`, `SUM(Alacakli)`; `Islemtipi IN (1,3)`.
  Zirve'nin varsayılan mizanı kapanış fişini (`Fistur = 9`) içermez; dahil edilirse kapanmış yılda
  tüm bakiyeler sıfır çıkar.
- **Muavin**: tek `Gmkod` için `Fistar, Refno, Refno2` sırasıyla yürüyen `SUM(Borclu - Alacakli)`.
- Açılış bakiyeleri 1 Ocak tarihli açılış fişindedir (`Fistur = 0`), ayrı bir devir tablosu yoktur.
