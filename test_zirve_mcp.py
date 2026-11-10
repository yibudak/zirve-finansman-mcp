"""Veritabanı gerektirmeyen kontroller: `python test_zirve_mcp.py` veya `pytest`."""

from datetime import date, datetime

import zirve_mcp as z


def test_like_escape():
    assert z.like_escape("50%_[x]\\") == "50\\%\\_\\[x]\\\\"


def test_vkn():
    assert z.vkn_norm(" '1234567890 ") == "1234567890"
    assert z.vkn_sql("y.Vergino") == "REPLACE(y.Vergino, '''', '')"
    assert z.clean({"Vergino": "'1234567890", "Aciklama": "O'Neil"}) == {"Vergino": "1234567890", "Aciklama": "O'Neil"}


def test_clean():
    row = {
        "a": " x ",
        "b": None,
        "c": "",
        "d": 1.005000001,
        "e": datetime(2026, 1, 2),
        "f": datetime(2026, 1, 2, 3, 4, 5),
        "g": date(2026, 1, 2),
        "h": 0,
    }
    assert z.clean(row) == {
        "a": "x",
        "d": 1.01,
        "e": "2026-01-02",
        "f": "2026-01-02 03:04:05",
        "g": "2026-01-02",
        "h": 0,
    }


def test_databases_and_resolve():
    dbs = z.group_databases(["A_2025", "A_2026", "A_2026T", "A_GENEL", "B]x_2024", "zirvegenel", "master"])
    assert dbs == {"A": {2025: "A_2025", 2026: "A_2026"}, "B]x": {2024: "B]x_2024"}}
    assert z.resolve_db(dbs, "A", None) == "[A_2026]"  # varsayılan: en son yıl
    assert z.resolve_db(dbs, "A", 2025) == "[A_2025]"
    assert z.resolve_db(dbs, "B]x", 2024) == "[B]]x_2024]"  # köşeli parantez kaçışı
    for firma, yil in [("A", 2020), ("A_2026]; DROP TABLE x--", None), (None, None)]:
        try:
            z.resolve_db(dbs, firma, yil)
        except ValueError:
            continue
        raise AssertionError((firma, yil))


def test_where():
    w = z.Where("x = 1")
    w.like("c", "a%b", prefix=True)
    w.like("d", None)
    w.dates("t", "2026-01-01", None)
    assert w.sql == "x = 1 AND c LIKE %s ESCAPE '\\' AND t >= %s"
    assert w.params == ["a\\%b%", date(2026, 1, 1)]
    try:
        z.parse_date("01.02.2026", "baslangic")
    except ValueError:
        pass
    else:
        raise AssertionError


def test_parse_datetime():
    assert z.parse_datetime("2026-11-10", "x") == datetime(2026, 11, 10)
    assert z.parse_datetime("2026-11-10 14:00:00", "x") == datetime(2026, 11, 10, 14)
    assert z.parse_datetime(None, "x") is None
    try:
        z.parse_datetime("dün", "x")
    except ValueError:
        pass
    else:
        raise AssertionError


def test_fold_and_text():
    assert z.fold("CİVATA ışık") == "CiVATA işik"
    w = z.Where()
    w.text("h.Aciklama", "CIVATA%")
    assert "Latin1_General_CI_AI LIKE %s" in w.sql and w.params == ["%CIVATA\\%%"]


def test_errors_reach_client():
    """Beklenen hatalar ToolError olarak çıkmalı; SDK başka istisnaların mesajını gizler."""
    from mcp.server.mcpserver.exceptions import ToolError

    for call in (lambda: z.yevmiye_ara(), lambda: z.mizan(seviye="orta"), lambda: z.muavin("100", offset=-1)):
        try:
            call()
        except ToolError as e:
            assert str(e)
        else:
            raise AssertionError


def test_page_and_clamp():
    assert z.paging(10**6, 0) == (z.MAX_LIMIT, 0) and z.paging(0, 5) == (1, 5)
    try:
        z.paging(10, -1)
    except ValueError:
        pass
    else:
        raise AssertionError
    assert z.page([1, 2, 3], 2, 0) == {"kayitlar": [1, 2], "offset": 0, "devami_var": True, "sonraki_offset": 2}
    assert z.page([1], 2, 4) == {"kayitlar": [1], "offset": 4, "devami_var": False}


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
