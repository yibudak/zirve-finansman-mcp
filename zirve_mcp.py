"""Zirve Finansman için unofficial, salt-okunur MCP sunucusu (MSSQL).

Yalnızca sabit, parametreli SELECT sorguları çalıştırır; serbest SQL aracı yoktur.
Asıl salt-okunur güvencesi veritabanı tarafında verilmelidir: sql/create_readonly_login.sql
"""

import os
import re
from contextlib import contextmanager
from datetime import date, datetime

import pymssql
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

MAX_LIMIT = 500
MAX_VOUCHER_LINES = 2000
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
        "içeren kodlar vardır; kodu olduğu gibi kullanın. Tarihler YYYY-AA-GG biçimindedir."
    ),
)

READ_ONLY = ToolAnnotations(readOnlyHint=True)


# ---------- saf yardımcılar ----------


def like_escape(s: str) -> str:
    """LIKE joker karakterlerini etkisizleştirir (ESCAPE '\\' ile kullanılır)."""
    return re.sub(r"([\\%_\[])", r"\\\1", s)


def clean(row: dict) -> dict:
    """Boş alanları atar, float'ları kuruşa yuvarlar, tarihleri ISO metne çevirir."""
    out = {}
    for k, v in row.items():
        if isinstance(v, str):
            v = v.strip()
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
        return date.fromisoformat(s)
    except ValueError:
        raise ValueError(f"{name} YYYY-AA-GG biçiminde olmalı: {s!r}") from None


def clamp(limit: int) -> int:
    return max(1, min(int(limit), MAX_LIMIT))


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
        raise RuntimeError("ZIRVE_SERVER ortam değişkeni tanımlı değil (ör. 10.0.0.5 veya HOST\\ZRVSQL2014)")
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


def databases(cur) -> dict[str, dict[int, str]]:
    cur.execute("SELECT name FROM sys.databases WHERE state = 0 AND database_id > 4 AND HAS_DBACCESS(name) = 1")
    return group_databases(r["name"] for r in cur.fetchall())


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


# ---------- araçlar ----------


@mcp.tool(annotations=READ_ONLY)
def firmalari_listele() -> dict:
    """Zirve'deki firmaları ve her firmanın muhasebe verisi bulunan yıllarını listeler."""
    with cursor() as cur:
        dbs = databases(cur)
    return {
        "firmalar": [{"firma": f, "yillar": sorted(y)} for f, y in sorted(dbs.items())],
        "varsayilan_firma": os.environ.get("ZIRVE_FIRMA"),
    }


@mcp.tool(annotations=READ_ONLY)
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

    arama: hesap adında geçen metin. kod_on_eki: hesap kodu başlangıcı (ör. "120" veya "320.01").
    vergi_no: hesaba tanımlı VKN/TCKN. sadece_detay: yalnızca kayıt girilebilen alt hesaplar.
    """
    limit = clamp(limit)
    w = Where()
    w.like("h.Aciklama", arama)
    w.like("h.Kod", kod_on_eki, prefix=True)
    if vergi_no:
        w.add("(h.Vergikimlikno = %s OR h.Tckimlikno = %s)", vergi_no.strip(), vergi_no.strip())
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
    return page(rows, limit, offset)


@mcp.tool(annotations=READ_ONLY)
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
        raise ValueError('seviye "detay" veya "ana" olmalı')
    limit = clamp(limit)
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
    return {**page(rows, limit, offset), "toplam": total[0] if total else {}}


@mcp.tool(annotations=READ_ONLY)
def muavin(
    hesap_kodu: str,
    baslangic: str | None = None,
    bitis: str | None = None,
    limit: int = 200,
    offset: int = 0,
    firma: str | None = None,
    yil: int | None = None,
) -> dict:
    """Muavin (hesap ekstresi): tek bir hesabın hareketleri ve yürüyen bakiyesi.

    hesap_kodu: tam hesap kodu (ör. "120.00010"; hesap_ara ile bulunur). baslangic verilirse o tarihten
    önceki hareketler "devreden" olarak özetlenir. Bakiye pozitifse borç, negatifse alacak bakiyesidir.
    """
    limit = clamp(limit)
    kod = hesap_kodu.strip()
    b = parse_date(baslangic, "baslangic")
    w = Where(YEV_FILTER, "y.Gmkod = %s")
    w.params.append(kod)
    w.dates("y.Fistar", baslangic, bitis)
    with cursor() as cur:
        db = resolve_db(databases(cur), firma, yil)
        hesap = fetch(
            cur,
            f"SELECT h.Kod, h.Aciklama, h.Vergikimlikno, h.Tckimlikno FROM {db}.dbo.hesplan h WHERE h.Kod = %s",
            [kod],
        )
        devir = 0.0
        if b:
            cur.execute(
                f"""SELECT SUM(ISNULL(y.Borclu,0)) - SUM(ISNULL(y.Alacakli,0)) AS d
                            FROM {db}.dbo.yevmiye y WHERE {YEV_FILTER} AND y.Gmkod = %s AND y.Fistar < %s""",
                (kod, b),
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
    total["KapanisBakiyesi"] = round(devir + total.get("Borc", 0) - total.get("Alacak", 0), 2)
    return {"hesap": hesap[0] if hesap else None, "devreden": devir, **page(rows, limit, offset), "toplam": total}


@mcp.tool(annotations=READ_ONLY)
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
    aciklama: satır açıklamasında geçen metin. hesap_on_eki: hesap kodu başlangıcı (ör. "120", "600").
    tutar_min/tutar_max: satırın TL tutarı (borç veya alacak); tam tutar için ikisine aynı değeri verin.
    baslangic/bitis: fiş tarihi aralığı. En az bir filtre verin.
    """
    limit = clamp(limit)
    w = Where(YEV_FILTER)
    w.like("y.Evrakno", evrak_no)
    w.like("y.Aciklama", aciklama)
    w.like("y.Gmkod", hesap_on_eki, prefix=True)
    if vergi_no:
        w.add("y.Vergino = %s", vergi_no.strip())
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
    return page(rows, limit, offset)


FIS_COLS = (
    "f.Refno, f.Fistar, f.Fistur, f.Fisno, f.Yevno, f.Btop, f.Atop, f.Nereden, f.Ekaciklama, f.Ozkod, f.Kayittarihi"
)


@mcp.tool(annotations=READ_ONLY)
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

    baslangic/bitis: fiş tarihi aralığı. kayit_tarihinden_sonra: kayıt zamanı (Kayittarihi) bu tarihten
    (YYYY-AA-GG) sonra olan fişler; "son kontrolden beri ne girildi" sorusu için. fis_no: fiş numarası
    (baştaki sıfırlar olmadan da verilebilir). kullanici: fişi kaydeden Zirve kullanıcısı (Ozkod).
    Btop/Atop fişin borç/alacak toplamıdır. Satırlar için fis_getir kullanın.
    """
    limit = clamp(limit)
    w = Where()
    w.dates("f.Fistar", baslangic, bitis)
    k = parse_date(kayit_tarihinden_sonra, "kayit_tarihinden_sonra")
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
    return page(rows, limit, offset)


@mcp.tool(annotations=READ_ONLY)
def fis_getir(refno: int, firma: str | None = None, yil: int | None = None) -> dict:
    """Tek bir muhasebe fişini başlığı ve tüm yevmiye satırlarıyla getirir.

    refno: fişin Refno değeri (yevmiye_ara, muavin ve fisleri_listele sonuçlarında bulunur).
    Not: Zirve'de bir fiş düzeltildiğinde Refno değişir; kalıcı referans olarak Fisno kullanın.
    """
    with cursor() as cur:
        db = resolve_db(databases(cur), firma, yil)
        head = fetch(cur, f"SELECT {FIS_COLS} FROM {db}.dbo.fisbil f WHERE f.Refno = %s", [int(refno)])
        if not head:
            raise ValueError(f"Fiş bulunamadı: Refno={refno}")
        lines = fetch(
            cur,
            f"""
            SELECT TOP {MAX_VOUCHER_LINES + 1} {YEV_COLS} FROM {db}.dbo.yevmiye y
            WHERE y.Refno = %s ORDER BY y.Refno2""",
            [int(refno)],
        )
    return {"fis": head[0], "satirlar": lines[:MAX_VOUCHER_LINES], "satirlar_kesildi": len(lines) > MAX_VOUCHER_LINES}


def main():
    mcp.run()


if __name__ == "__main__":
    main()
