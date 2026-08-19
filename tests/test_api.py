"""Faz 7 REST uçları: oturum geçmişi, arama, kullanım grafiği, kalıcı hafıza.

`/ws/chat` ve `/api/quota` gibi mevcut uçların TestClient testi yok (proje
şimdiye kadar bunları canlı/manuel doğruladı) — burada sadece Faz 7'de
eklenen, UI'sı olmayan yeni uçlar test ediliyor.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.memory.store import ensure_session, save_message, set_note
from app.quota.tracker import record_usage


def test_sessions_empty_by_default(workspace: Path) -> None:
    client = TestClient(app)
    resp = client.get("/api/sessions")
    assert resp.status_code == 200
    assert resp.json() == {"sessions": []}


def test_sessions_lists_with_derived_title(workspace: Path) -> None:
    ensure_session("s1")
    save_message("s1", "user", "ULL-Bot'a merhaba de")
    save_message("s1", "assistant", "Merhaba!")

    client = TestClient(app)
    resp = client.get("/api/sessions")
    rows = resp.json()["sessions"]
    assert len(rows) == 1
    assert rows[0]["id"] == "s1"
    assert rows[0]["title"] == "ULL-Bot'a merhaba de"
    assert rows[0]["message_count"] == 2


def test_session_messages_returns_full_record(workspace: Path) -> None:
    ensure_session("s1")
    save_message("s1", "user", "merhaba")
    save_message("s1", "tool", "dir listing", tool_name="list_dir")

    client = TestClient(app)
    resp = client.get("/api/sessions/s1/messages")
    body = resp.json()
    assert body["session_id"] == "s1"
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["user", "tool"]


def test_search_finds_matching_message(workspace: Path) -> None:
    ensure_session("s1")
    save_message("s1", "user", "pdf dosyalarını listele")
    ensure_session("s2")
    save_message("s2", "user", "hava durumu nasıl")

    client = TestClient(app)
    resp = client.get("/api/search", params={"q": "pdf"})
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["session_id"] == "s1"


def test_search_empty_query_returns_nothing(workspace: Path) -> None:
    client = TestClient(app)
    resp = client.get("/api/search", params={"q": ""})
    assert resp.json()["results"] == []


def test_usage_graph_groups_by_day_and_provider(workspace: Path) -> None:
    record_usage(provider="groq", model="m", prompt_tokens=10, completion_tokens=5)
    record_usage(provider="groq", model="m", prompt_tokens=1, completion_tokens=1)
    record_usage(provider="openrouter", model="m", prompt_tokens=2, completion_tokens=2)

    client = TestClient(app)
    resp = client.get("/api/usage/graph")
    points = resp.json()["points"]
    by_provider = {p["provider"]: p for p in points}
    assert by_provider["groq"]["requests"] == 2
    assert by_provider["groq"]["tokens"] == 17
    assert by_provider["openrouter"]["requests"] == 1


def test_memory_notes_roundtrip(workspace: Path) -> None:
    set_note("preferred_shell", "fish")
    client = TestClient(app)

    resp = client.get("/api/memory")
    assert resp.json()["notes"] == [
        {"key": "preferred_shell", "value": "fish", "updated_at": resp.json()["notes"][0]["updated_at"]}
    ]

    resp = client.delete("/api/memory/preferred_shell")
    assert resp.json() == {"ok": True, "key": "preferred_shell"}
    assert client.get("/api/memory").json()["notes"] == []


def test_memory_delete_missing_key_reports_false(workspace: Path) -> None:
    client = TestClient(app)
    resp = client.delete("/api/memory/nope")
    assert resp.json() == {"ok": False, "key": "nope"}


# --- kullanıcı teması -------------------------------------------------------


def test_tema_dosyasi_yoksa_bos_css_donuyor(workspace: Path) -> None:
    """404 DEĞİL: tarayıcı konsolunu kirletir ve "bozuk" izlenimi verir."""
    client = TestClient(app)
    response = client.get("/theme.css")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert response.text == ""


def test_tema_dosyasi_varsa_icerigi_donuyor(workspace: Path, tmp_path, monkeypatch) -> None:
    from app.settings import settings

    theme = tmp_path / "tema.css"
    theme.write_text(":root { --accent: #00ff9c; }", encoding="utf-8")
    monkeypatch.setattr(settings, "user_theme", str(theme))

    client = TestClient(app)
    response = client.get("/theme.css")
    assert response.status_code == 200
    assert "--accent: #00ff9c" in response.text


def test_tema_style_css_ten_SONRA_yukleniyor():
    """Sıra önemli: kullanıcının kuralları varsayılanı ezebilmeli."""
    from pathlib import Path

    html = (Path(__file__).resolve().parent.parent / "web" / "index.html").read_text(
        encoding="utf-8"
    )
    assert html.index("/static/style.css") < html.index("/theme.css")


def test_index_ve_tema_da_onbellekten_dogrulaniyor(workspace: Path) -> None:
    """`/` ve `/theme.css` `/static` altında DEĞİL — mount başlığı onlara geçmez.

    Sahada bu boşluk şuna yol açtı: sunucu güncellendi, `style.css` ve
    modüller yeni geldi ama tarayıcı ESKİ `index.html`i diskten servis etti;
    kullanıcı yeni kategorileri ve yeni mail düzenini hiç göremedi, üstelik
    yeni CSS eski HTML'e uygulandığı için ekran bozuk göründü.
    """
    client = TestClient(app)
    for path in ("/", "/theme.css"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers.get("cache-control") == "no-cache", (
            f"{path} önbellek doğrulaması istemiyor"
        )


def test_statik_dosyalar_onbellekten_dogrulaniyor(workspace: Path) -> None:
    """`Cache-Control: no-cache` olmadan tarayıcı ESKİ arayüzü gösteriyor.

    WebKit/Chromium `Cache-Control` yokken "heuristic freshness" uyguluyor:
    dosyanın yaşına bakıp sunucuya hiç sormadan diskten servis ediyor.
    Sahada yaşandı — CSS/JS değişti, uygulama yeniden başlatıldı, ekranda
    eski sürüm çıktı. `no-cache` "önbelleğe alma" değil "kullanmadan önce
    SOR" demek; dosya değişmediyse 304 dönüyor.
    """
    client = TestClient(app)
    response = client.get("/static/style.css")
    assert response.status_code == 200
    assert response.headers.get("cache-control") == "no-cache"
