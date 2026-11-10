# Değişiklik günlüğü

Biçim [Keep a Changelog](https://keepachangelog.com/tr/1.1.0/) düzenini, sürüm numaraları
[Semantic Versioning](https://semver.org/lang/tr/) kurallarını izler.

## [0.1.0] - 2026-11-10

### Eklendi

- Salt okunur yedi araç: `firmalari_listele`, `hesap_ara`, `mizan`, `muavin`, `yevmiye_ara`,
  `fisleri_listele`, `fis_getir`.
- Salt okunur SQL kullanıcısı açan betik (`sql/create_readonly_login.sql`).
- Zirve veritabanı şema notları (`docs/zirve-sema.md`).
- Hata nedenleri istemciye metin olarak ulaşır (`ToolError`).
- `mizan` ve `muavin` kapanış fişini varsayılan olarak saymaz (`kapanis_dahil`).
- Metin aramaları büyük-küçük harf ve Türkçe karakter ayrımı gözetmez; VKN kesme işaretinden arındırılır.

[0.1.0]: https://github.com/yibudak/zirve-finansman-mcp/releases/tag/v0.1.0
