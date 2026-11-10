<p align="center"><img src="assets/banner.png" alt="zirve-finansman-mcp: unofficial read-only MCP server" width="100%"></p>

# zirve-finansman-mcp

[![CI](https://github.com/yibudak/zirve-finansman-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/yibudak/zirve-finansman-mcp/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/zirve-finansman-mcp)](https://pypi.org/project/zirve-finansman-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/zirve-finansman-mcp)](https://pypi.org/project/zirve-finansman-mcp/)
[![Lisans: AGPL-3.0](https://img.shields.io/badge/lisans-AGPL--3.0-blue.svg)](LICENSE)

Zirve Finansman'ın genel muhasebe verisini yapay zekâ ajanlarına açan, salt okunur bir
[MCP](https://modelcontextprotocol.io) sunucusu. Zirve'nin MSSQL veritabanına bağlanır. Ajan hesap
planını, fişleri ve yevmiye satırlarını okur, mizan ve muavin alır.

Ön muhasebeyi başka bir sistemde tutup resmî defteri Zirve'de işleyen şirketler için yazıldı.
İki sistemi de gören bir ajan, eksik ya da tutarı uyuşmayan kayıtları kendisi bulur.

> Unofficial bir projedir. Zirve Yazılım'la bir bağı yoktur, Zirve Yazılım tarafından desteklenmez.
> "Zirve" adı sahibinin markasıdır.

## Araçlar

| Araç | İşlevi |
|---|---|
| `firmalari_listele` | Firmaları ve muhasebe verisi bulunan yılları listeler |
| `hesap_ara` | Hesap planında ada, kod ön ekine veya VKN/TCKN'ye göre arar |
| `mizan` | Hesap bazında borç, alacak ve bakiye verir (detay ya da ana hesap seviyesinde, tarih aralığıyla) |
| `muavin` | Bir hesabın hareketlerini devreden ve yürüyen bakiyeyle verir |
| `yevmiye_ara` | Yevmiye satırlarını evrak no, VKN, açıklama, hesap, tutar ve tarihe göre arar |
| `fisleri_listele` | Fiş başlıklarını listeler, belli bir tarihten sonra kaydedilenleri süzer |
| `fis_getir` | Bir fişi başlığı ve bütün satırlarıyla getirir |

Araçların hiçbiri veri yazmaz. Serbest SQL çalıştıran bir araç da bilerek eklenmedi.

## Kurulum

Python 3.10 veya üstü, [uv](https://docs.astral.sh/uv/) ve Zirve'nin SQL Server'ına ağ erişimi gerekir.

### 1. Salt okunur bir SQL kullanıcısı açın

Sunucuyu Zirve'nin kendi SQL kullanıcısıyla ya da `sa` ile çalıştırmayın. Verinin değişmeyeceğini
veritabanı yetkisi garanti etmeli. [`sql/create_readonly_login.sql`](sql/create_readonly_login.sql)
betiğini bir SQL yöneticisine çalıştırtın. Betik yalnızca `SELECT` yetkisi olan bir kullanıcı açar.

### 2. Bağlantı bilgisini bulun

Zirve çoğu kurulumda `ZRVSQL2014` gibi adlandırılmış bir instance ve dinamik bir TCP portu kullanır.
Portu sunucuda şu sorguyla görürsünüz:

```sql
SELECT DISTINCT local_tcp_port FROM sys.dm_exec_connections WHERE local_tcp_port IS NOT NULL;
```

Dinamik port, SQL servisi yeniden başlayınca değişebilir. İki çözümü var: SQL Server Configuration
Manager'dan sabit port verin ya da `ZIRVE_SERVER` değerini `HOST\INSTANCE` biçiminde yazın. İkinci yol
için sunucuda SQL Browser servisi çalışmalı ve UDP 1434 açık olmalı.

### 3. MCP istemcisine ekleyin

```json
{
  "mcpServers": {
    "zirve": {
      "command": "uvx",
      "args": ["zirve-finansman-mcp"],
      "env": {
        "ZIRVE_SERVER": "192.0.2.10",
        "ZIRVE_PORT": "1433",
        "ZIRVE_USER": "zirve_mcp_ro",
        "ZIRVE_PASSWORD": "parolanız",
        "ZIRVE_FIRMA": "FIRMA_ADI"
      }
    }
  }
}
```

Paketi PyPI yerine doğrudan depodan çalıştırmak için `args` değerini şöyle yazın:

```json
["--from", "git+https://github.com/yibudak/zirve-finansman-mcp", "zirve-finansman-mcp"]
```

### Ortam değişkenleri

| Değişken | Açıklama |
|---|---|
| `ZIRVE_SERVER` | Sunucu adresi veya `HOST\INSTANCE`. Zorunlu |
| `ZIRVE_PORT` | TCP portu. Instance adı yazdıysanız gerekmez |
| `ZIRVE_USER`, `ZIRVE_PASSWORD` | Salt okunur SQL kullanıcısı ve parolası |
| `ZIRVE_FIRMA` | Varsayılan firma. Zirve giriş ekranındaki "Firma Tanıtıcı Adı" |
| `ZIRVE_QUERY_TIMEOUT` | Sorgu zaman aşımı, saniye cinsinden. Varsayılan 30 |

## Bilmeniz gerekenler

Kapsam genel muhasebeyle sınırlı. Ticari modül (fatura, stok, cari kartlar), bordro ve beyannameler
okunmaz.

Sorgular kilit almadan okur (`READ UNCOMMITTED`). Bu sayede muhasebe programı hiç beklemez. Bedeli şu:
o anda kaydedilmekte olan bir fişi yarım görebilirsiniz.

Zirve tutarları `float` olarak saklar. Sunucu sonuçları kuruşa yuvarlar.

`mizan` aracı, Zirve'nin "kapanış fişi hariç" mizanı gibi kapanış fişini varsayılan olarak saymaz.
Kapanış fişi dahil edilirse kapanmış yıllarda bütün bakiyeler sıfır çıkar.

Zirve'de bir fiş düzeltilince `Refno` değişir. Fişi sonradan yeniden bulmak için `Fisno` kullanın.

Proje, Zirve Finansman V9 ve SQL Server 2014 üzerinde denendi.

## Şema notları

Zirve veritabanı şemasını belgelemiyor. Çalışan bir kurulumu inceleyerek çıkardığım notlar
[`docs/zirve-sema.md`](docs/zirve-sema.md) dosyasında: veritabanlarının düzeni, çekirdek tablolar, kod
alanlarının anlamları. Hangi bilginin doğrulandığı, hangisinin veriden çıkarıldığı orada işaretli.

## Geliştirme

```sh
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Testler veritabanı gerektirmez. Katkı için ayrıntılar [CONTRIBUTING.md](CONTRIBUTING.md) dosyasında.

## Güvenlik

Bir güvenlik açığı bulursanız herkese açık issue açmayın. GitHub'daki "Report a vulnerability"
bağlantısından özel olarak bildirin. Ayrıntılar [SECURITY.md](SECURITY.md) dosyasında.

## Lisans

[AGPL-3.0-or-later](LICENSE). Sunucuyu değiştirip ağ üzerinden başkalarına kullandırırsanız değiştirdiğiniz
kaynak kodu da aynı lisansla paylaşmanız gerekir.
