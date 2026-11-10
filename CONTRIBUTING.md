# Katkı rehberi

Hata bildirimi, şema notu ve kod katkısı için teşekkürler.

## Değişmeyen kurallar

- Sunucu salt okunur kalır. Veri yazan ya da serbest SQL çalıştıran bir araç kabul edilmez.
- Sorgular parametreli olur. Kullanıcı girdisi SQL metnine gömülmez. Veritabanı adı yalnızca sunucudan
  okunan listeden seçilir.
- Depoya gerçek şirket verisi girmez: firma adı, cari adı, VKN, tutar, sunucu adresi, ekran görüntüsü.
  Örneklerde uydurma veri kullanın.
- Zirve'nin kendi dosyaları (kurulumdaki XML, PDF, CHM) depoya eklenmez.

## Geliştirme ortamı

```sh
uv sync
uv run pytest
uv run ruff check .
uv run ruff format .
```

Testler veritabanına bağlanmaz. Sorgu değiştirdiyseniz kendi Zirve kurulumunuzda, salt okunur bir
kullanıcıyla deneyin ve hangi Zirve sürümünde denediğinizi PR'da yazın.

## Şema bilgisi eklerken

`docs/zirve-sema.md` dosyasında her bilgi nereden geldiğiyle işaretlidir. Zirve'nin yardım dosyasına ya
da kendi prosedürlerine dayanan bilgiye `[doğrulandı]`, veri dağılımından çıkardığınız bilgiye
`[çıkarım]` yazın. Emin olmadığınız bir kod anlamını kesinmiş gibi yazmayın.

## Sürüm çıkarma

`pyproject.toml` içindeki sürümü ve `CHANGELOG.md` dosyasını güncelleyin, sonra `v` ile başlayan bir
etiket gönderin (`v0.2.0` gibi). Yayın iş akışı paketi derleyip PyPI'ye yükler ve GitHub sürümünü açar.
