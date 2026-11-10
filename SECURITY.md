# Güvenlik

## Açık bildirimi

Bir güvenlik açığı bulduysanız herkese açık issue açmayın. Depo sayfasında Security sekmesindeki
"Report a vulnerability" bağlantısından özel bildirim gönderin. Bildirimde gerçek muhasebe verisi
paylaşmayın.

## Güvenlik modeli

Sunucu muhasebe veritabanına bağlandığı için şu sınırlarla tasarlandı:

- Araçlar yalnızca sabit, parametreli `SELECT` sorguları çalıştırır. Serbest SQL aracı yoktur.
- Veritabanı adı kullanıcı girdisinden üretilmez, sunucudaki veritabanı listesinden seçilir.
- Kod tarafındaki bu önlemler tek başına güvence sayılmaz. Sunucuyu yalnızca `SELECT` yetkisi olan bir
  SQL kullanıcısıyla çalıştırın: [`sql/create_readonly_login.sql`](sql/create_readonly_login.sql).
- Bağlantı parolası ortam değişkeninden okunur. Parolayı depoya ya da paylaşılan yapılandırma
  dosyalarına yazmayın.

Sunucunun döndürdüğü muhasebe verisi, bağlandığınız yapay zekâ istemcisine ve onun sağlayıcısına gider.
Bunu şirketinizin veri politikasına göre değerlendirin.
