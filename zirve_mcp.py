"""Zirve Finansman için unofficial, salt-okunur MCP sunucusu (MSSQL).

Yalnızca sabit, parametreli SELECT sorguları çalıştırır; serbest SQL aracı yoktur.
Asıl salt-okunur güvencesi veritabanı tarafında verilmelidir: sql/create_readonly_login.sql
"""

import functools
import os
import re
from contextlib import contextmanager
from datetime import date, datetime

import pymssql
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

MAX_LIMIT = 500
DB_RE = re.compile(r"^(?P<firma>.+)_(?P<yil>\d{4})$")  # ör. FIRMA_2026 (…T ve …_GENEL eşleşmez)

# Yevmiye satırı için her yıl veritabanında bulunan çekirdek kolonlar.
YEV_COLS = (
    "y.Refno, y.Refno2, y.Fistar, y.Fistur, y.Fisno, y.Yevno, y.Gmkod, y.Tip, y.Aciklama, "
    "y.Borclu, y.Alacakli, y.Dovizadi, y.Doviz, y.Miktar, y.Evraktarihi, y.Evrakno, y.serino, "
    "y.Vergino, y.Belge_Turu, y.Belge_Turu_Aciklamasi, y.Odeme_Sekli, y.Vade_Tarihi, y.Masrafmerkezi"
)
# Zirve'nin kendi muavin prosedürü (spMuavin) de yalnızca bu işlem tiplerini sayar.
YEV_FILTER = "ISNULL(y.Islemtipi, 1) IN (1, 3)"

mcp = MCPServer(
    "zirve-finansman",
    instructions=(
        "Zirve Finansman genel muhasebe verilerine SALT-OKUNUR erişim. Her firma+yıl ayrı bir "
        "veritabanıdır; önce firmalari_listele ile firma/yıl seçin. Temel kavramlar: hesap planı "
        "(hesplan), fiş başlığı (fisbil, anahtar Refno), yevmiye satırı (yevmiye, anahtar Refno+Refno2). "
        "Tip: B=borç, C=alacak. Fistur: 0=açılış, 1=tahsil, 2=tediye, 3=mahsup, 9=kapanış "
        "(ayrıca 4=yansıtma, 5=enflasyon düzeltmesi fişlerinde görülür). "
        "Dovizadi: 1=TL, 2=USD, 3=EUR (boş=TL); Doviz alanı döviz tutarıdır, Borclu/Alacakli her zaman TL. "
        "Belge_Turu: 1=fatura, 8=diğer (adı Belge_Turu_Aciklamasi alanındadır, ör. banka ekstresi), "
        "9=belge yok; diğer kodların ve Odeme_Sekli kodlarının anlamı doğrulanmamıştır, ham kod olarak yorumlayın. "
        "Evrakno fatura/belge numarasıdır ve dış sistemlerle eşleştirme için en iyi anahtardır; "
        "Vergino karşı tarafın VKN/TCKN'sidir. Hesap kodlarında ayırıcı çoğunlukla '.' olsa da ',' ve '-' "
        "içeren kodlar vardır; kodu olduğu gibi kullanın. Tarihler YYYY-AA-GG biçimindedir. "
        "mizan ve muavin, Zirve'nin varsayılanı gibi kapanış fişini saymaz (kapanis_dahil ile değişir). "
        "Demirbaş (sabit kıymet) kartları yıldan bağımsızdır: demirbas_ara ve demirbas_getir."
    ),
)

READ_ONLY = ToolAnnotations(readOnlyHint=True)


# ---------- saf yardımcılar ----------


def like_escape(s: str) -> str:
    """LIKE joker karakterlerini etkisizleştirir (ESCAPE '\\' ile kullanılır)."""
    return re.sub(r"([\\%_\[])", r"\\\1", s)


VKN_COLS = {"Vergino", "Vergikimlikno", "Tckimlikno", "AlisVergiNo", "SatisVergiNo"}


def vkn_norm(s: str) -> str:
    """VKN/TCKN'deki kesme işaretlerini ve boşlukları atar (Zirve'de bir kısmı '1234567890 biçiminde saklı)."""
    return re.sub(r"['\s]", "", s)


def vkn_sql(column: str) -> str:
    return f"REPLACE({column}, '''', '')"


def clean(row: dict) -> dict:
    """Boş alanları atar, float'ları kuruşa yuvarlar, tarihleri ISO metne çevirir."""
    out = {}
    for k, v in row.items():
        if isinstance(v, str):
            v = vkn_norm(v) if k in VKN_COLS else v.strip()
        if v is None or v == "":
            continue
        if isinstance(v, float):
            v = round(v, 2) + 0.0  # -0.0 -> 0.0
        elif isinstance(v, datetime):
            v = v.date().isoformat() if v.time() == datetime.min.time() else v.isoformat(sep=" ", timespec="seconds")
        elif isinstance(v, date):
            v = v.isoformat()
        elif not isinstance(v, (int, bool, str)):
            v = str(v)
        out[k] = v
    return out


def parse_date(s: str | None, name: str) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s.strip())
    except ValueError:
        raise ValueError(f"{name} YYYY-AA-GG biçiminde olmalı: {s!r}") from None


def parse_datetime(s: str | None, name: str) -> datetime | None:
    """Yalnız tarih (YYYY-AA-GG) ya da tarih ve saat (YYYY-AA-GG SS:DD[:SS]) kabul eder."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.strip())
    except ValueError:
        raise ValueError(f"{name} YYYY-AA-GG veya YYYY-AA-GG SS:DD:SS biçiminde olmalı: {s!r}") from None


def paging(limit: int, offset: int) -> tuple[int, int]:
    """limit'i 1..MAX_LIMIT aralığına çeker, negatif offset'i reddeder."""
    if int(offset) < 0:
        raise ValueError(f"offset negatif olamaz: {offset}")
    return max(1, min(int(limit), MAX_LIMIT)), int(offset)


def fold(s: str) -> str:
    """Türkçe i/ı/İ/I ayrımını arama için tek harfe indirir (SQL tarafındaki fold_sql ile aynı kural)."""
    return s.replace("İ", "i").replace("ı", "i")


def fold_sql(column: str) -> str:
    """Kolonu büyük-küçük harf, aksan ve Türkçe i ayrımı gözetmeden karşılaştırılır hâle getirir.

    Böylece "CIVATA", "civata" ve "CİVATA" aynı kaydı bulur; "SIRKET" de "ŞİRKET" ile eşleşir.
    """
    return f"REPLACE(REPLACE({column}, N'İ', N'i'), N'ı', N'i') COLLATE Latin1_General_CI_AI"


def group_databases(names) -> dict[str, dict[int, str]]:
    """Veritabanı adlarını {firma: {yıl: veritabanı}} olarak gruplar."""
    out: dict[str, dict[int, str]] = {}
    for n in names:
        m = DB_RE.match(n)
        if m:
            out.setdefault(m["firma"], {})[int(m["yil"])] = n
    return out


def resolve_db(dbs: dict[str, dict[int, str]], firma: str | None, yil: int | None) -> str:
    """Firma+yıl için köşeli parantezle alıntılanmış veritabanı adını döndürür.

    Ad yalnızca sunucudan okunan listeden seçilir; kullanıcı girdisi SQL'e hiç gömülmez.
    """
    firma = firma or os.environ.get("ZIRVE_FIRMA")
    if not firma:
        if len(dbs) != 1:
            raise ValueError(f"firma belirtin. Mevcut firmalar: {sorted(dbs)}")
        firma = next(iter(dbs))
    if firma not in dbs:
        raise ValueError(f"Firma bulunamadı: {firma!r}. Mevcut firmalar: {sorted(dbs)}")
    years = dbs[firma]
    yil = int(yil) if yil else max(years)
    if yil not in years:
        raise ValueError(f"{firma} için {yil} yılı yok. Mevcut yıllar: {sorted(years)}")
    return "[" + years[yil].replace("]", "]]") + "]"


def genel_db(names, firma: str | None) -> str:
    """Firmanın yıldan bağımsız {FIRMA}_GENEL veritabanının alıntılanmış adını döndürür (demirbaşlar orada durur)."""
    names = set(names)
    firma = firma or os.environ.get("ZIRVE_FIRMA")
    firmalar = sorted(n[: -len("_GENEL")] for n in names if n.endswith("_GENEL"))
    if not firma:
        if len(firmalar) != 1:
            raise ValueError(f"firma belirtin. Mevcut firmalar: {firmalar}")
        firma = firmalar[0]
    if f"{firma}_GENEL" not in names:
        raise ValueError(
            f"{firma!r} için _GENEL veritabanı yok ya da bu kullanıcının erişimi yok. Erişilebilenler: {firmalar}"
        )
    return "[" + f"{firma}_GENEL".replace("]", "]]") + "]"


class Where:
    """Parametreli WHERE biriktirici."""

    def __init__(self, *clauses: str):
        self.clauses = list(clauses)
        self.params: list = []

    def add(self, clause: str, *params):
        self.clauses.append(clause)
        self.params.extend(params)

    def like(self, column: str, value: str | None, prefix: bool = False):
        if value:
            pattern = like_escape(value.strip()) + "%"
            self.add(f"{column} LIKE %s ESCAPE '\\'", pattern if prefix else "%" + pattern)

    def text(self, column: str, value: str | None):
        """Metin içinde arama: büyük-küçük harf, aksan ve Türkçe i ayrımı gözetmez."""
        if value:
            self.add(f"{fold_sql(column)} LIKE %s ESCAPE '\\'", "%" + like_escape(fold(value.strip())) + "%")

    def dates(self, column: str, baslangic: str | None, bitis: str | None):
        b, e = parse_date(baslangic, "baslangic"), parse_date(bitis, "bitis")
        if b:
            self.add(f"{column} >= %s", b)
        if e:
            self.add(f"{column} <= %s", e)

    @property
    def sql(self) -> str:
        return " AND ".join(self.clauses) if self.clauses else "1=1"


# ---------- veritabanı ----------


@contextmanager
def cursor():
    server = os.environ.get("ZIRVE_SERVER")
    if not server:
        raise ValueError("ZIRVE_SERVER ortam değişkeni tanımlı değil (ör. 10.0.0.5 veya HOST\\ZRVSQL2014)")
    kwargs = {}
    if os.environ.get("ZIRVE_PORT"):
        kwargs["port"] = os.environ["ZIRVE_PORT"]
    conn = pymssql.connect(
        server=server,
        user=os.environ.get("ZIRVE_USER"),
        password=os.environ.get("ZIRVE_PASSWORD"),
        database="master",
        login_timeout=10,
        timeout=int(os.environ.get("ZIRVE_QUERY_TIMEOUT", "30")),
        as_dict=True,
        autocommit=True,
        read_only=True,
        appname="zirve-finansman-mcp",
        **kwargs,
    )
    try:
        cur = conn.cursor()
        # Muhasebe programını asla bekletmemek için kilit almadan okur. Bedeli: kaydedilmekte olan
        # bir fiş yarım görülebilir (dirty read).
        cur.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")
        yield cur
    finally:
        conn.close()


def db_names(cur) -> list[str]:
    cur.execute("SELECT name FROM sys.databases WHERE state = 0 AND database_id > 4 AND HAS_DBACCESS(name) = 1")
    return [r["name"] for r in cur.fetchall()]


def databases(cur) -> dict[str, dict[int, str]]:
    return group_databases(db_names(cur))


def fetch(cur, sql: str, params=()) -> list[dict]:
    cur.execute(sql, tuple(params))
    return [clean(r) for r in cur.fetchall()]


def page(rows: list[dict], limit: int, offset: int) -> dict:
    """limit+1 satır çekilmiş sonuçtan sayfa bilgisi üretir."""
    more = len(rows) > limit
    out = {"kayitlar": rows[:limit], "offset": offset, "devami_var": more}
    if more:
        out["sonraki_offset"] = offset + limit
    return out


PAGING = "OFFSET %s ROWS FETCH NEXT %s ROWS ONLY"
BOS_YIL_NOTU = "Bu firma/yıl veritabanında hiç yevmiye kaydı yok; verisi olan yıllar için firmalari_listele."


def has_data(cur, db: str) -> bool:
    cur.execute(f"SELECT TOP 1 1 AS x FROM {db}.dbo.yevmiye")
    return cur.fetchone() is not None


def note_if_empty(cur, db: str, out: dict) -> dict:
    """Sonuç boşsa ve yılın kendisi boşsa bunu söyler; ajan "kayıt yok" ile "yıl boş"u ayırabilsin."""
    if not out["kayitlar"] and not has_data(cur, db):
        out["not"] = BOS_YIL_NOTU
    return out


def db_error_text(e: Exception) -> str:
    msg = e.args[-1] if e.args else e
    if isinstance(msg, bytes):
        msg = msg.decode("utf-8", "replace")
    return " ".join(str(msg).split())[:500]


def tool(fn):
    """Aracı kaydeder ve beklenen hataları, metni istemciye ulaşan ToolError'a çevirir.

    MCP SDK'sı ToolError dışındaki istisnaların mesajını istemciden gizler; ajan sebebi göremeyince
    aynı çağrıyı tekrarlar.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as e:
            raise ToolError(str(e)) from None
        except pymssql.Error as e:
            raise ToolError(f"Veritabanı hatası: {db_error_text(e)}") from e

    return mcp.tool(annotations=READ_ONLY)(wrapper)


# ---------- araçlar ----------


@tool
def firmalari_listele() -> dict:
    """Zirve'deki firmaları listeler: yillar yevmiye kaydı bulunan, bos_yillar veritabanı olup boş olan yıllar."""
    firmalar = []
    with cursor() as cur:
        for firma, years in sorted(databases(cur).items()):
            dolu, bos = [], []
            for yil in sorted(years):
                try:
                    (dolu if has_data(cur, resolve_db({firma: years}, firma, yil)) else bos).append(yil)
                except pymssql.Error:  # yevmiye tablosu yok ya da okuma yetkisi verilmemiş
                    bos.append(yil)
            firmalar.append({"firma": firma, "yillar": dolu, "bos_yillar": bos})
    return {"firmalar": firmalar, "varsayilan_firma": os.environ.get("ZIRVE_FIRMA")}


@tool
def hesap_ara(
    arama: str | None = None,
    kod_on_eki: str | None = None,
    vergi_no: str | None = None,
    sadece_detay: bool = True,
    limit: int = 50,
    offset: int = 0,
    firma: str | None = None,
    yil: int | None = None,
) -> dict:
    """Hesap planında arama yapar (cari hesaplar dahil; ör. 120=alıcılar, 320=satıcılar).

    arama: hesap adında geçen metin (büyük-küçük harf ve Türkçe karakter ayrımı gözetmez).
    kod_on_eki: hesap kodu başlangıcı (ör. "120" veya "320.01").
    vergi_no: hesaba tanımlı VKN/TCKN. sadece_detay: yalnızca kayıt girilebilen alt hesaplar.
    Hesap kartlarının çoğunda VKN boştur; vergi_no ile sonuç çıkmazsa yevmiye_ara(vergi_no=...,
    hesap_on_eki="120" veya "320") ile carinin hesap kodunu yevmiye satırlarından bulun.
    """
    limit, offset = paging(limit, offset)
    w = Where()
    w.text("h.Aciklama", arama)
    w.like("h.Kod", kod_on_eki, prefix=True)
    if vergi_no:
        vkn = vkn_norm(vergi_no)
        w.add(f"({vkn_sql('h.Vergikimlikno')} = %s OR {vkn_sql('h.Tckimlikno')} = %s)", vkn, vkn)
    if sadece_detay:
        w.add("h.Detay = 'E'")
    with cursor() as cur:
        db = resolve_db(databases(cur), firma, yil)
        rows = fetch(
            cur,
            f"""
            SELECT h.Kod, h.Aciklama, h.Detay, h.Vergikimlikno, h.Tckimlikno, h.Vergidairesi, h.Ulkekodu, h.Grup
            FROM {db}.dbo.hesplan h WHERE {w.sql}
            ORDER BY h.Kod {PAGING}""",
            [*w.params, offset, limit + 1],
        )
        return note_if_empty(cur, db, page(rows, limit, offset))


@tool
def mizan(
    kod_on_eki: str | None = None,
    baslangic: str | None = None,
    bitis: str | None = None,
    seviye: str = "detay",
    sifir_bakiyeler: bool = False,
    kapanis_dahil: bool = False,
    limit: int = 200,
    offset: int = 0,
    firma: str | None = None,
    yil: int | None = None,
) -> dict:
    """Mizan: hesap bazında borç/alacak toplamları ve bakiye (yevmiye satırlarından hesaplanır).

    kod_on_eki: yalnızca bu kodla başlayan hesaplar (ör. "120"). baslangic/bitis: fiş tarihi aralığı
    (boşsa tüm yıl; açılış fişi 1 Ocak tarihlidir). seviye: "detay" (alt hesap) veya "ana" (ilk 3 hane).
    Bakiye = Borc - Alacak (pozitif borç bakiyesi). toplam alanı sayfalamadan bağımsız genel toplamdır.
    kapanis_dahil: Zirve'nin "kapanış fişi hariç" mizanı gibi, varsayılan olarak kapanış fişi (Fistur 9)
    sayılmaz; True verilirse kapanmış yıllarda tüm bakiyeler sıfırlanır.
    """
    if seviye not in ("detay", "ana"):
        raise ValueError(f'seviye "detay" veya "ana" olmalı: {seviye!r}')
    if seviye == "ana" and kod_on_eki and len(kod_on_eki.strip()) > 3:
        raise ValueError(
            'seviye="ana" ilk 3 haneye göre gruplar; kod_on_eki en çok 3 karakter olabilir. '
            'Alt kırılımın toplamı için seviye="detay" verip sonuçtaki toplam alanına bakın.'
        )
    limit, offset = paging(limit, offset)
    kod = "y.Gmkod" if seviye == "detay" else "LEFT(y.Gmkod, 3)"
    w = Where(YEV_FILTER)
    if not kapanis_dahil:
        w.add("y.Fistur <> '9'")
    w.like("y.Gmkod", kod_on_eki, prefix=True)
    w.dates("y.Fistar", baslangic, bitis)
    having = "" if sifir_bakiyeler else "HAVING ABS(SUM(ISNULL(y.Borclu,0)) - SUM(ISNULL(y.Alacakli,0))) >= 0.005"
    with cursor() as cur:
        db = resolve_db(databases(cur), firma, yil)
        rows = fetch(
            cur,
            f"""
            SELECT k.Kod, h.Aciklama, k.Borc, k.Alacak, k.Borc - k.Alacak AS Bakiye
            FROM (SELECT {kod} AS Kod, SUM(ISNULL(y.Borclu,0)) AS Borc, SUM(ISNULL(y.Alacakli,0)) AS Alacak
                  FROM {db}.dbo.yevmiye y WHERE {w.sql} GROUP BY {kod} {having}) k
            LEFT JOIN {db}.dbo.hesplan h ON h.Kod = k.Kod
            ORDER BY k.Kod {PAGING}""",
            [*w.params, offset, limit + 1],
        )
        total = fetch(
            cur,
            f"""
            SELECT SUM(ISNULL(y.Borclu,0)) AS Borc, SUM(ISNULL(y.Alacakli,0)) AS Alacak,
                   SUM(ISNULL(y.Borclu,0)) - SUM(ISNULL(y.Alacakli,0)) AS Bakiye
            FROM {db}.dbo.yevmiye y WHERE {w.sql}""",
            w.params,
        )
        return note_if_empty(cur, db, {**page(rows, limit, offset), "toplam": total[0] if total else {}})


@tool
def muavin(
    hesap_kodu: str,
    baslangic: str | None = None,
    bitis: str | None = None,
    kapanis_dahil: bool = False,
    limit: int = 200,
    offset: int = 0,
    firma: str | None = None,
    yil: int | None = None,
) -> dict:
    """Muavin (hesap ekstresi): tek bir hesabın hareketleri ve yürüyen bakiyesi.

    hesap_kodu: tam hesap kodu (ör. "120.00010"; hesap_ara ile bulunur). baslangic verilirse o tarihten
    önceki hareketler "devreden" olarak özetlenir. Bakiye pozitifse borç, negatifse alacak bakiyesidir.
    kapanis_dahil: mizan ile tutarlı olsun diye kapanış fişi (Fistur 9) varsayılan olarak sayılmaz; True
    verilirse kapanmış yılda kapanış bakiyesi sıfır çıkar. Ana hesap ya da olmayan kod verilirse hata döner.
    """
    limit, offset = paging(limit, offset)
    kod = hesap_kodu.strip()
    b = parse_date(baslangic, "baslangic")
    base = Where(YEV_FILTER, "y.Gmkod = %s")
    base.params.append(kod)
    if not kapanis_dahil:
        base.add("y.Fistur <> '9'")
    w = Where(*base.clauses)
    w.params = list(base.params)
    w.dates("y.Fistar", baslangic, bitis)
    with cursor() as cur:
        db = resolve_db(databases(cur), firma, yil)
        hesap = fetch(
            cur,
            f"""SELECT h.Kod, h.Aciklama, h.Detay, h.Vergikimlikno, h.Tckimlikno
                FROM {db}.dbo.hesplan h WHERE h.Kod = %s""",
            [kod],
        )
        devir = 0.0
        if b:
            cur.execute(
                f"""SELECT SUM(ISNULL(y.Borclu,0)) - SUM(ISNULL(y.Alacakli,0)) AS d
                            FROM {db}.dbo.yevmiye y WHERE {base.sql} AND y.Fistar < %s""",
                (*base.params, b),
            )
            devir = round(cur.fetchone()["d"] or 0.0, 2)
        rows = fetch(
            cur,
            f"""
            SELECT * FROM (
                SELECT {YEV_COLS}, %s + SUM(ISNULL(y.Borclu,0) - ISNULL(y.Alacakli,0))
                           OVER (ORDER BY y.Fistar, y.Refno, y.Refno2 ROWS UNBOUNDED PRECEDING) AS Bakiye
                FROM {db}.dbo.yevmiye y WHERE {w.sql}) t
            ORDER BY t.Fistar, t.Refno, t.Refno2 {PAGING}""",
            [devir, *w.params, offset, limit + 1],
        )
        total = fetch(
            cur,
            f"""
            SELECT COUNT(*) AS SatirSayisi, SUM(ISNULL(y.Borclu,0)) AS Borc, SUM(ISNULL(y.Alacakli,0)) AS Alacak
            FROM {db}.dbo.yevmiye y WHERE {w.sql}""",
            w.params,
        )[0]
        if not total.get("SatirSayisi") and not devir:
            if not hesap:
                bos = "" if has_data(cur, db) else " " + BOS_YIL_NOTU
                raise ValueError(f"Hesap kodu bulunamadı: {kod!r}. Doğru kodu hesap_ara ile bulun.{bos}")
            if hesap[0].get("Detay") == "H":
                raise ValueError(
                    f"{kod!r} bir ana hesap, üzerinde hareket olmaz. Alt hesap kodu verin (hesap_ara(kod_on_eki=...)); "
                    f"ana hesabın toplamı için mizan(kod_on_eki=...), hareketleri için yevmiye_ara(hesap_on_eki=...)."
                )
    total["KapanisBakiyesi"] = round(devir + total.get("Borc", 0) - total.get("Alacak", 0), 2)
    return {"hesap": hesap[0] if hesap else None, "devreden": devir, **page(rows, limit, offset), "toplam": total}


@tool
def yevmiye_ara(
    evrak_no: str | None = None,
    vergi_no: str | None = None,
    aciklama: str | None = None,
    hesap_on_eki: str | None = None,
    tutar_min: float | None = None,
    tutar_max: float | None = None,
    baslangic: str | None = None,
    bitis: str | None = None,
    limit: int = 100,
    offset: int = 0,
    firma: str | None = None,
    yil: int | None = None,
) -> dict:
    """Yevmiye satırlarında arama yapar; dış sistem kayıtlarını Zirve'de bulmak için ana araçtır.

    evrak_no: fatura/belge numarasında geçen metin. vergi_no: satırdaki VKN/TCKN (tam eşleşme).
    aciklama: satır açıklamasında geçen metin (büyük-küçük harf ve Türkçe karakter ayrımı gözetmez).
    hesap_on_eki: hesap kodu başlangıcı (ör. "120", "600").
    tutar_min/tutar_max: satırın TL tutarı (borç veya alacak); tam tutar için ikisine aynı değeri verin.
    baslangic/bitis: fiş tarihi aralığı. En az bir filtre verin.
    """
    limit, offset = paging(limit, offset)
    w = Where(YEV_FILTER)
    w.like("y.Evrakno", evrak_no)
    w.text("y.Aciklama", aciklama)
    w.like("y.Gmkod", hesap_on_eki, prefix=True)
    if vergi_no:
        w.add(f"{vkn_sql('y.Vergino')} = %s", vkn_norm(vergi_no))
    tutar = "(ISNULL(y.Borclu,0) + ISNULL(y.Alacakli,0))"
    if tutar_min is not None:
        w.add(f"{tutar} >= %s", float(tutar_min) - 0.005)
    if tutar_max is not None:
        w.add(f"{tutar} <= %s", float(tutar_max) + 0.005)
    w.dates("y.Fistar", baslangic, bitis)
    if len(w.clauses) == 1:
        raise ValueError("En az bir filtre verin")
    with cursor() as cur:
        db = resolve_db(databases(cur), firma, yil)
        rows = fetch(
            cur,
            f"""
            SELECT {YEV_COLS} FROM {db}.dbo.yevmiye y WHERE {w.sql}
            ORDER BY y.Fistar, y.Refno, y.Refno2 {PAGING}""",
            [*w.params, offset, limit + 1],
        )
        return note_if_empty(cur, db, page(rows, limit, offset))


FIS_COLS = (
    "f.Refno, f.Fistar, f.Fistur, f.Fisno, f.Yevno, f.Btop, f.Atop, f.Nereden, f.Ekaciklama, f.Ozkod, f.Kayittarihi"
)


@tool
def fisleri_listele(
    baslangic: str | None = None,
    bitis: str | None = None,
    kayit_tarihinden_sonra: str | None = None,
    fis_no: str | None = None,
    kullanici: str | None = None,
    limit: int = 100,
    offset: int = 0,
    firma: str | None = None,
    yil: int | None = None,
) -> dict:
    """Muhasebe fişlerinin başlıklarını listeler (en son kaydedilen önce).

    baslangic/bitis: fiş tarihi aralığı. kayit_tarihinden_sonra: kayıt zamanı (Kayittarihi) bu andan
    sonra olan fişler; YYYY-AA-GG ya da saatle YYYY-AA-GG SS:DD:SS. "Son kontrolden beri ne girildi"
    sorusu için. fis_no: fiş numarası
    (baştaki sıfırlar olmadan da verilebilir). kullanici: fişi kaydeden Zirve kullanıcısı (Ozkod).
    Btop/Atop fişin borç/alacak toplamıdır. Satırlar için fis_getir kullanın.
    """
    limit, offset = paging(limit, offset)
    w = Where()
    w.dates("f.Fistar", baslangic, bitis)
    k = parse_datetime(kayit_tarihinden_sonra, "kayit_tarihinden_sonra")
    if k:
        w.add("f.Kayittarihi >= %s", k)
    if fis_no:
        w.add("f.Fisno = %s", fis_no.strip().zfill(9))
    if kullanici:
        w.add("f.Ozkod = %s", kullanici.strip())
    with cursor() as cur:
        db = resolve_db(databases(cur), firma, yil)
        rows = fetch(
            cur,
            f"""
            SELECT {FIS_COLS} FROM {db}.dbo.fisbil f WHERE {w.sql}
            ORDER BY f.Kayittarihi DESC, f.Refno DESC {PAGING}""",
            [*w.params, offset, limit + 1],
        )
        return note_if_empty(cur, db, page(rows, limit, offset))


@tool
def fis_getir(refno: int, limit: int = 200, offset: int = 0, firma: str | None = None, yil: int | None = None) -> dict:
    """Tek bir muhasebe fişini başlığı ve yevmiye satırlarıyla getirir (satırlar sayfalıdır).

    refno: fişin Refno değeri (yevmiye_ara, muavin ve fisleri_listele sonuçlarında bulunur).
    satir_sayisi fişteki toplam satırdır; devami_var ise sonraki_offset ile devam edin.
    Not: Zirve'de bir fiş düzeltildiğinde Refno değişir; kalıcı referans olarak Fisno kullanın.
    """
    limit, offset = paging(limit, offset)
    with cursor() as cur:
        db = resolve_db(databases(cur), firma, yil)
        head = fetch(cur, f"SELECT {FIS_COLS} FROM {db}.dbo.fisbil f WHERE f.Refno = %s", [int(refno)])
        if not head:
            bos = "" if has_data(cur, db) else " " + BOS_YIL_NOTU
            raise ValueError(f"Fiş bulunamadı: Refno={refno}. Fiş düzeltildiyse Refno değişmiş olabilir.{bos}")
        cur.execute(f"SELECT COUNT(*) AS n FROM {db}.dbo.yevmiye y WHERE y.Refno = %s", (int(refno),))
        n = cur.fetchone()["n"]
        lines = fetch(
            cur,
            f"SELECT {YEV_COLS} FROM {db}.dbo.yevmiye y WHERE y.Refno = %s ORDER BY y.Refno2 {PAGING}",
            [int(refno), offset, limit + 1],
        )
    out = page(lines, limit, offset)
    out["satirlar"] = out.pop("kayitlar")
    return {"fis": head[0], "satir_sayisi": n, **out}


# Demirbaş kartı (dbas). Zirve'nin kısaltılmış kolon adları okunur adlarla döndürülür.
DEMIRBAS_COLS = (
    "d.Dkodu AS Kod, d.Dacik AS Aciklama, d.Datar AS AlisTarihi, d.Daeno AS AlisEvrakNo, d.Datutar AS AlisTutari, "
    "d.Dakdv AS AlisKdv, d.Daafir AS AlinanFirma, d.Avergino AS AlisVergiNo, d.Miktar, "
    "d.Amt AS AmortismanTipi, d.Amo AS AmortismanOraniYuzde, d.Ams AS AmortismanSuresiYil, d.Kismia AS KistAmortisman, "
    "d.St AS SatisTarihi, d.Stut AS SatisTutari, d.Skdv AS SatisKdv, d.Sfno AS SatisFaturaNo, d.Sfir AS SatilanFirma, "
    "d.Svergino AS SatisVergiNo, d.Demmuhhk AS HesapKodu, d.Hpbada AS BirikmisAmortismanHesabi, "
    "d.Hpdaa AS AmortismanGiderHesabi, d.Ozkod AS OzelKod"
)
# Yıllık değerleme/amortisman satırı (dbayd).
DEMIRBAS_YIL_COLS = (
    "a.Hy AS Yil, a.Ydeh AS YenidenDegerleme, a.Ydo AS YenidenDegerlemeOrani, a.Dgb AS GirisBedeli, "
    "a.Dobad AS DonemBasiDeger, a.Dobam AS DonemBasiBirikmisAmortisman, a.Donad AS DonemBasiNetDeger, "
    "a.Ada AS DegerArtisi, a.Bada AS BirikmisAmortismanArtisi, a.Dsbad AS DegerlemeSonrasiDeger, "
    "a.Dsba AS DegerlemeSonrasiBirikmisAmortisman, a.Dsnad AS DegerlemeSonrasiNetDeger, a.Daf AS DegerArtisFonu, "
    "a.Daa AS DonemAmortismani, a.Ba AS DonemSonuBirikmisAmortisman, a.Gddd AS DevredenNetDeger"
)


@tool
def demirbas_ara(
    arama: str | None = None,
    kod_on_eki: str | None = None,
    durum: str = "hepsi",
    alis_baslangic: str | None = None,
    alis_bitis: str | None = None,
    limit: int = 50,
    offset: int = 0,
    firma: str | None = None,
) -> dict:
    """Demirbaş (sabit kıymet) kartlarında arama yapar. Kartlar yıldan bağımsızdır, yil parametresi yoktur.

    arama: demirbaş adında geçen metin. kod_on_eki: demirbaş kodu başlangıcı (çoğu kurulumda kod, demirbaşın
    muhasebe hesap koduyla aynıdır; ör. "253", "254.01"). durum: "hepsi", "aktif" (satılmamış) veya "satilan".
    alis_baslangic/alis_bitis: alış tarihi aralığı. toplam alanı sayfalamadan bağımsız adet ve alış tutarıdır.
    HesapKodu demirbaşın muhasebe hesabıdır; hareketleri için muavin(hesap_kodu=...) kullanın.
    """
    if durum not in ("hepsi", "aktif", "satilan"):
        raise ValueError(f'durum "hepsi", "aktif" veya "satilan" olmalı: {durum!r}')
    limit, offset = paging(limit, offset)
    w = Where()
    w.text("d.Dacik", arama)
    w.like("d.Dkodu", kod_on_eki, prefix=True)
    if durum != "hepsi":
        w.add("d.St IS NULL" if durum == "aktif" else "d.St IS NOT NULL")
    b, e = parse_date(alis_baslangic, "alis_baslangic"), parse_date(alis_bitis, "alis_bitis")
    if b:
        w.add("d.Datar >= %s", b)
    if e:
        w.add("d.Datar <= %s", e)
    with cursor() as cur:
        db = genel_db(db_names(cur), firma)
        rows = fetch(
            cur,
            f"SELECT {DEMIRBAS_COLS} FROM {db}.dbo.dbas d WHERE {w.sql} ORDER BY d.Dkodu {PAGING}",
            [*w.params, offset, limit + 1],
        )
        total = fetch(
            cur, f"SELECT COUNT(*) AS Adet, SUM(d.Datutar) AS AlisTutari FROM {db}.dbo.dbas d WHERE {w.sql}", w.params
        )
    return {**page(rows, limit, offset), "toplam": total[0] if total else {}}


@tool
def demirbas_getir(kod: str, firma: str | None = None) -> dict:
    """Bir demirbaş kartını yıllık değerleme ve amortisman satırlarıyla getirir.

    kod: demirbaş kodu (demirbas_ara sonucundaki Kod). yillar: her yıl için dönem başı değer, yeniden değerleme,
    dönem amortismanı (DonemAmortismani), birikmiş amortisman ve devreden net değer.
    Dikkat: yıllık satırları Zirve, Demirbaşlar > Raporlar ekranı çalıştırıldığında üretir; bazı yıllar eksik
    ya da güncel olmayabilir. Kesin tutar için muhasebe kaydına (muavin) bakın.
    """
    kod = kod.strip()
    with cursor() as cur:
        db = genel_db(db_names(cur), firma)
        kart = fetch(cur, f"SELECT {DEMIRBAS_COLS} FROM {db}.dbo.dbas d WHERE d.Dkodu = %s", [kod])
        if not kart:
            raise ValueError(f"Demirbaş bulunamadı: {kod!r}. Doğru kodu demirbas_ara ile bulun.")
        yillar = fetch(cur, f"SELECT {DEMIRBAS_YIL_COLS} FROM {db}.dbo.dbayd a WHERE a.Dk = %s ORDER BY a.Hy", [kod])
    return {"demirbas": kart[0], "yillar": yillar}


def main():
    mcp.run()


if __name__ == "__main__":
    main()
