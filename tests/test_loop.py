"""Ajan döngüsü testleri: adım limiti, onay akışı, kırpma, geçmiş bütünlüğü.

Gerçek LLM çağrılmaz — `FakeLLM` önceden yazılmış cevapları sırayla döndürür,
böylece döngünün davranışı deterministik olarak sınanabilir.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.agent.llm import LLMResponse, ToolCall
from app.agent.loop import AgentLoop
from app.db.connection import get_connection


class FakeLLM:
    def __init__(self, *responses: LLMResponse) -> None:
        self.responses = list(responses)
        self.seen_messages: list[list[dict[str, Any]]] = []
        self.seen_models: list[str] = []
        self.calls = 0

    async def complete(
        self, messages, tools, on_token, *, model: str | None = None, provider: str = ""
    ) -> LLMResponse:
        self.calls += 1
        self.seen_messages.append([dict(m) for m in messages])
        self.seen_models.append(model or "")
        response = self.responses.pop(0) if self.responses else LLMResponse(content="bitti")
        if isinstance(response, Exception):
            raise response  # testler 429/hata senaryosunu böyle kuruyor
        response.provider = provider
        if response.content:
            await on_token(response.content)
        return response


def tool_response(name: str, call_id: str = "c1", **arguments: Any) -> LLMResponse:
    return LLMResponse(
        tool_calls=[ToolCall(id=call_id, name=name, arguments=json.dumps(arguments))]
    )


class Recorder:
    """emit/approve callback'lerini yakalar."""

    def __init__(self, answers: list[bool] | None = None) -> None:
        self.events: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        self.answers = list(answers or [])

    async def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    async def approve(self, request: dict[str, Any]) -> bool:
        self.requests.append(request)
        return self.answers.pop(0) if self.answers else False

    def of_type(self, kind: str) -> list[dict[str, Any]]:
        return [event for event in self.events if event["type"] == kind]


def build(workspace: Path, llm: FakeLLM, rec: Recorder, **kwargs) -> AgentLoop:
    """Testler `task_type`i zorlar (`"default"`) — aksi hâlde her testin mesajı

    gerçek sınıflandırıcıdan geçip farklı bir görev tipi zincirine düşerdi ve
    bu dosyadaki testler (sağlayıcı devri, adım limiti, onay akışı vb.)
    sınıflandırmayla ilgisiz oldukları hâlde kırılgan olurdu. Sınıflandırmanın
    kendisi `tests/test_classifier.py`de, döngüye kablolanışı ise
    `test_loop_uses_classified_task_type` gibi adı geçen testlerde ayrıca
    sınanıyor.
    """
    kwargs.setdefault("task_type", "default")
    return AgentLoop(
        session_id="s-test",
        llm=llm,
        emit=rec.emit,
        approve=rec.approve,
        cwd=workspace,
        dry_run=False,
        **kwargs,
    )


# --- temel akış -----------------------------------------------------------


async def test_loop_runs_safe_tool_then_answers(workspace: Path) -> None:
    (workspace / "rapor.pdf").write_text("x", encoding="utf-8")
    llm = FakeLLM(
        tool_response("list_dir", path=str(workspace)),
        LLMResponse(content="Bir adet pdf var: rapor.pdf"),
    )
    rec = Recorder()
    answer = await build(workspace, llm, rec).run("klasördeki pdf'leri listele")

    assert answer == "Bir adet pdf var: rapor.pdf"
    assert rec.requests == [], "salt okunur listeleme onay istememeli"
    results = rec.of_type("tool_result")
    assert len(results) == 1 and results[0]["ok"]
    assert "rapor.pdf" in results[0]["output"]
    assert rec.of_type("done")


async def test_unknown_tool_does_not_crash_loop(workspace: Path) -> None:
    llm = FakeLLM(tool_response("ucan_araba"), LLMResponse(content="olmadı"))
    rec = Recorder()
    answer = await build(workspace, llm, rec).run("dene")
    assert answer == "olmadı"
    assert "Böyle bir araç yok" in rec.of_type("tool_result")[0]["output"]


async def test_malformed_arguments_are_reported(workspace: Path) -> None:
    llm = FakeLLM(
        LLMResponse(tool_calls=[ToolCall(id="c1", name="read_file", arguments="{bozuk")]),
        LLMResponse(content="peki"),
    )
    rec = Recorder()
    await build(workspace, llm, rec).run("dene")
    assert "geçerli JSON değil" in rec.of_type("tool_result")[0]["output"]


# --- adım limiti ----------------------------------------------------------


async def test_step_limit_asks_user_and_stops(workspace: Path) -> None:
    llm = FakeLLM(*[tool_response("list_dir", path=str(workspace)) for _ in range(10)])
    rec = Recorder(answers=[False])
    agent = build(workspace, llm, rec, max_steps=2)
    result = await agent.run("durmadan çalış")

    assert agent.steps_used == 2, "limit aşılmamalı"
    assert len(rec.requests) == 1
    assert rec.requests[0]["type"] == "continue_request"
    assert "Adım limitine" in result
    assert rec.of_type("stopped")


async def test_step_limit_extends_when_approved(workspace: Path) -> None:
    llm = FakeLLM(*[tool_response("list_dir", path=str(workspace)) for _ in range(10)])
    rec = Recorder(answers=[True, False])
    agent = build(workspace, llm, rec, max_steps=2)
    await agent.run("devam et")

    assert agent.steps_used == 4, "onay sonrası bir tur daha çalışmalıydı"
    assert len(rec.requests) == 2


# --- güvenlik akışı -------------------------------------------------------


async def test_blocked_command_never_executes(workspace: Path) -> None:
    canary = workspace / "kurban.txt"
    canary.write_text("duruyorum", encoding="utf-8")
    llm = FakeLLM(
        tool_response("run_shell", command=f"sudo rm -rf {canary}"),
        LLMResponse(content="reddedildi"),
    )
    rec = Recorder(answers=[True])  # kullanıcı "evet" dese bile sorulmamalı
    await build(workspace, llm, rec).run("her şeyi sil")

    assert canary.exists(), "yasaklı komut dosyayı silmemeliydi"
    assert rec.requests == [], "blocked komut için onay bile sorulmamalı"
    output = rec.of_type("tool_result")[0]["output"]
    assert "REDDEDİLDİ" in output


async def test_confirm_denied_does_not_execute(workspace: Path) -> None:
    target = workspace / "silinecek.txt"
    target.write_text("x", encoding="utf-8")
    llm = FakeLLM(
        tool_response("run_shell", command=f"rm {target}"),
        LLMResponse(content="tamam, silmedim"),
    )
    rec = Recorder(answers=[False])
    await build(workspace, llm, rec).run("şunu sil")

    assert target.exists()
    assert len(rec.requests) == 1
    assert rec.requests[0]["type"] == "approval_request"
    assert rec.requests[0]["risk"] == "confirm"
    assert "onaylamadı" in rec.of_type("tool_result")[0]["output"]


async def test_confirm_approved_executes(workspace: Path) -> None:
    target = workspace / "silinecek.txt"
    target.write_text("x", encoding="utf-8")
    llm = FakeLLM(
        tool_response("run_shell", command=f"rm {target}"),
        LLMResponse(content="sildim"),
    )
    rec = Recorder(answers=[True])
    await build(workspace, llm, rec).run("şunu sil")

    assert not target.exists(), "onaylanan komut çalışmalıydı"


async def test_approval_request_shows_command_and_paths(workspace: Path) -> None:
    llm = FakeLLM(tool_response("run_shell", command="touch yeni.txt"), LLMResponse(content="ok"))
    rec = Recorder(answers=[False])
    await build(workspace, llm, rec).run("dosya oluştur")

    request = rec.requests[0]
    assert request["summary"] == "touch yeni.txt"
    assert str(workspace) in request["detail"]
    assert request["paths"] == [str(workspace)]


async def test_dry_run_blocks_writes(workspace: Path) -> None:
    target = workspace / "silinecek.txt"
    target.write_text("x", encoding="utf-8")
    llm = FakeLLM(tool_response("run_shell", command=f"rm {target}"), LLMResponse(content="ok"))
    rec = Recorder(answers=[True])
    agent = AgentLoop(
        session_id="s-dry",
        llm=llm,
        emit=rec.emit,
        approve=rec.approve,
        cwd=workspace,
        dry_run=True,
    )
    await agent.run("sil")

    assert target.exists(), "dry-run modunda dosya silinmemeliydi"
    assert "[dry-run]" in rec.of_type("tool_result")[0]["output"]
    assert rec.requests[0]["dry_run"] is True


# --- çıktı kırpma ---------------------------------------------------------


async def test_tool_output_is_truncated_before_going_back_to_model(workspace: Path) -> None:
    big = workspace / "devasa.txt"
    big.write_text("\n".join(f"{i} " + "x" * 100 for i in range(2000)), encoding="utf-8")
    llm = FakeLLM(tool_response("read_file", path=str(big)), LLMResponse(content="okudum"))
    rec = Recorder()
    await build(workspace, llm, rec).run("dosyayı oku")

    second_call = llm.seen_messages[1]
    tool_message = [m for m in second_call if m["role"] == "tool"][0]
    assert len(tool_message["content"]) < 4600, "araç çıktısı kırpılmamış"
    assert "kırpıldı" in tool_message["content"]
    # baş ve son korunmalı (ortadan kırpma)
    assert "1 xxx" in tool_message["content"]
    assert "1999 xxx" in tool_message["content"]


# --- geçmiş ---------------------------------------------------------------


async def test_history_is_persisted_and_reused(workspace: Path) -> None:
    llm = FakeLLM(LLMResponse(content="merhaba, ben ULL-Bot"))
    rec = Recorder()
    await build(workspace, llm, rec).run("selam")

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE session_id = 's-test' ORDER BY id"
        ).fetchall()
    assert [row["role"] for row in rows] == ["user", "assistant"]

    llm2 = FakeLLM(LLMResponse(content="evet, hatırlıyorum"))
    await build(workspace, llm2, Recorder()).run("ne demiştim?")
    first_call = llm2.seen_messages[0]
    roles = [m["role"] for m in first_call]
    assert roles == ["system", "user", "assistant", "user"]
    assert first_call[2]["content"] == "merhaba, ben ULL-Bot"


async def test_tool_messages_are_paired_with_their_call(workspace: Path) -> None:
    """`tool` mesajı, kendisini doğuran `tool_calls`'lu asistan mesajını izlemeli."""
    (workspace / "a.txt").write_text("x", encoding="utf-8")
    llm = FakeLLM(
        tool_response("list_dir", path=str(workspace)),
        LLMResponse(content="bitti"),
    )
    await build(workspace, llm, Recorder()).run("listele")

    second_call = llm.seen_messages[1]
    assistant_index = next(i for i, m in enumerate(second_call) if m.get("tool_calls"))
    tool_message = second_call[assistant_index + 1]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == second_call[assistant_index]["tool_calls"][0]["id"]


# --- Faz 4: sınıflandırıcının döngüye bağlanması --------------------------


async def test_first_step_uses_classified_task_type(
    workspace: Path, all_providers, local_enabled
) -> None:
    """"merhaba" trivial'e sınıflanır → trivial zincirinin ilk adayı seçilir

    (spec §9 Faz 4 kabul kriteri). `task_type` burada zorlanmıyor — gerçek
    sınıflandırıcı çalışıyor. `local_enabled`: Faz 5'ten beri trivial
    zincirinin ilk adayı `ollama` — bu test spesifik olarak hangi sağlayıcı
    değil, "ilk adım zincirin BAŞINI kullanıyor mu" diye bakıyor.
    """
    llm = FakeLLM(LLMResponse(content="selam"))
    rec = Recorder()
    agent = AgentLoop(
        session_id="s-classified",
        llm=llm,
        emit=rec.emit,
        approve=rec.approve,
        cwd=workspace,
        dry_run=False,
    )
    await agent.run("merhaba")

    classifications = rec.of_type("classification")
    assert classifications and classifications[0]["task_type"] == "trivial"

    from app.router.selector import get_routing_config

    trivial_first = get_routing_config().chain("desktop", "trivial")[0]
    switches = rec.of_type("model_switch")
    assert switches[0]["provider"] == trivial_first.provider


async def test_tool_step_uses_tool_use_task_type(workspace: Path, all_providers) -> None:
    """Ara adım (tool sonucu değerlendirme) her zaman `tool_use`e gider,

    ilk mesajın sınıflandırmasından bağımsız (spec §5.1/§5.2).
    """
    (workspace / "a.txt").write_text("x", encoding="utf-8")
    llm = FakeLLM(
        tool_response("list_dir", path=str(workspace)),
        LLMResponse(content="bitti"),
    )
    rec = Recorder()
    agent = AgentLoop(
        session_id="s-classified-2",
        llm=llm,
        emit=rec.emit,
        approve=rec.approve,
        cwd=workspace,
        dry_run=False,
    )
    await agent.run("listele lütfen bunu")

    from app.router.selector import get_routing_config

    tool_use_first = get_routing_config().chain("desktop", "tool_use")[0]
    switches = rec.of_type("model_switch")
    # İkinci adımda (tool sonucu değerlendirme) sağlayıcı hâlâ aynıysa rozet
    # tekrar basılmaz; asıl kanıt ikinci LLM çağrısında hangi modelin
    # kullanıldığı.
    assert llm.seen_models[-1] == tool_use_first.model


# --- boş sağlayıcı cevabı (canlı yakalandı) -------------------------------


async def test_bos_cevap_saglayici_hatasi_sayilir(
    workspace: Path, all_providers, local_enabled
) -> None:
    """Boş cevap turu bitirmemeli — sıradaki sağlayıcı denenmeli.

    OpenRouter'ın ücretsiz modeli ara sıra ne metin ne araç çağrısı
    döndürüyor. Eski davranış bunu geçerli bir "cevap" sayıp `done`
    yayımlıyordu: kullanıcı araçların çalıştığını görüyor, sonra hiçbir şey
    gelmiyordu. Canlı görüldü — "4 adım sürdü, sonuç vermedi".
    """
    llm = FakeLLM(LLMResponse(content=""), LLMResponse(content="işte karşılaştırma"))
    rec = Recorder()
    answer = await build(workspace, llm, rec).run("kulaklıkları karşılaştır")

    assert answer == "işte karşılaştırma"
    assert llm.calls == 2, "boş cevaptan sonra ikinci sağlayıcı denenmeliydi"
    done = rec.of_type("done")
    assert len(done) == 1 and rec.of_type("error") == []


async def test_sadece_bosluk_iceren_cevap_da_bos_sayilir(
    workspace: Path, all_providers, local_enabled
) -> None:
    """`"\\n\\n"` gibi bir cevap kullanıcı için boş ekrandan farksız."""
    llm = FakeLLM(LLMResponse(content="   \n  "), LLMResponse(content="gerçek cevap"))
    rec = Recorder()
    assert await build(workspace, llm, rec).run("dene") == "gerçek cevap"
    assert llm.calls == 2


async def test_hepsi_bos_donerse_kullanici_hata_goruyor(
    workspace: Path, all_providers, local_enabled
) -> None:
    """Sessizce bitmek en kötüsü: kullanıcı neden cevap gelmediğini bilmeli."""
    llm = FakeLLM(*[LLMResponse(content="") for _ in range(12)])
    rec = Recorder()
    answer = await build(workspace, llm, rec).run("dene")

    assert answer == ""
    errors = rec.of_type("error")
    assert errors and "boş cevap" in errors[0]["message"]
    assert rec.of_type("done") == [], "boş turda `done` yayımlanmamalı"


# --- bağlam bütçesi -------------------------------------------------------


def _tool_msg(index: int, size: int) -> dict:
    return {"role": "tool", "tool_call_id": str(index), "name": "fetch_url",
            "content": f"sayfa {index} " + "x" * size}


def test_eski_arac_ciktilari_kirpiliyor():
    """Groq ücretsiz katmanı dakikada 8000 token veriyor (ölçüldü).

    Her adımda konuşmanın tamamı yeniden gönderiliyor ve `fetch_url` 20.000
    karaktere kadar dönebiliyor; kırpma olmadan iki adımda bütçe doluyor,
    429 geliyor ve tur zayıf yerel modele düşüyordu.
    """
    from app.agent.loop import TOOL_RESULT_TRIM_CHARS, trim_context

    messages = [{"role": "system", "content": "S" * 4000},
                {"role": "user", "content": "karşılaştır"}]
    for index in range(6):
        messages.append(_tool_msg(index, 12000))

    trimmed = trim_context(messages)
    before = sum(len(m["content"]) for m in messages)
    after = sum(len(m["content"]) for m in trimmed)
    assert after < before / 2, f"kırpma yetersiz: {before} → {after}"

    eski = [m for m in trimmed if m["role"] == "tool"][:-2]
    assert all(len(m["content"]) < TOOL_RESULT_TRIM_CHARS + 100 for m in eski)
    assert all("kısaltıldı" in m["content"] for m in eski)


def test_son_iki_arac_ciktisi_tam_kaliyor():
    """Model üzerinde ÇALIŞTIĞI veriyi tam görmeli — kırpma sadece geçmişe."""
    from app.agent.loop import trim_context

    messages = [{"role": "user", "content": "x"}] + [_tool_msg(i, 9000) for i in range(4)]
    tools = [m for m in trim_context(messages) if m["role"] == "tool"]
    assert len(tools[-1]["content"]) > 9000
    assert len(tools[-2]["content"]) > 9000
    assert len(tools[-3]["content"]) < 1000


def test_kirpma_orijinal_mesajlari_bozmuyor():
    """Döngü kendi tam geçmişini korumalı; kırpma sadece modele giden kopyada.

    Bozulsaydı bir sonraki adımda "son iki çıktı" da kırpılmış olurdu ve
    model her adımda biraz daha körleşirdi.
    """
    from app.agent.loop import trim_context

    messages = [_tool_msg(i, 9000) for i in range(4)]
    trim_context(messages)
    assert all(len(m["content"]) > 9000 for m in messages)


def test_kirpma_kullanici_ve_asistan_mesajlarina_dokunmuyor():
    from app.agent.loop import trim_context

    messages = [
        {"role": "user", "content": "u" * 9000},
        {"role": "assistant", "content": "a" * 9000},
        _tool_msg(0, 9000),
        _tool_msg(1, 9000),
        _tool_msg(2, 9000),
    ]
    trimmed = trim_context(messages)
    assert len(trimmed[0]["content"]) == 9000
    assert len(trimmed[1]["content"]) == 9000


async def test_kisa_cooldownda_ayni_saglayici_bekleniyor(
    workspace: Path, all_providers, local_enabled
) -> None:
    """429 "5 saniye sonra" diyorsa bekle — zayıf modele düşme.

    Canlı: Groq TPM aşımında 5 saniyelik cooldown veriyordu, sistem hemen
    zincirin sonundaki yerel modele geçiyor ve tur çöpe gidiyordu.
    """
    from app.agent.llm import RateLimited

    llm = FakeLLM(
        RateLimited("429", provider="groq", retry_after="1"),
        LLMResponse(content="oldu"),
    )
    rec = Recorder()
    agent = build(workspace, llm, rec)

    answer = await agent.run("dene")

    assert answer == "oldu"
    switches = [e["provider"] for e in rec.of_type("model_switch")]
    assert switches[-1] == switches[0], (
        f"kısa cooldown'da aynı sağlayıcıya dönülmeliydi: {switches}"
    )


async def test_modele_giden_mesajlar_kirpilmis(workspace: Path, all_providers) -> None:
    """Kırpma gerçekten çağrı yoluna bağlı mı? (fonksiyonun var olması yetmez)

    `trim_context` doğru çalışıp `llm.complete`e bağlanmazsa hiçbir şey
    kazanmayız ve birim testleri yine geçer — bu test o boşluğu kapatıyor.
    """
    big = "y" * 20000
    (workspace / "buyuk.txt").write_text(big, encoding="utf-8")

    llm = FakeLLM(
        tool_response("read_file", "c1", path=str(workspace / "buyuk.txt")),
        tool_response("read_file", "c2", path=str(workspace / "buyuk.txt")),
        tool_response("read_file", "c3", path=str(workspace / "buyuk.txt")),
        LLMResponse(content="bitti"),
    )
    rec = Recorder()
    await build(workspace, llm, rec).run("dosyayı oku")

    son_istek = llm.seen_messages[-1]
    tool_mesajlari = [m for m in son_istek if m["role"] == "tool"]
    assert len(tool_mesajlari) >= 3
    assert "kısaltıldı" in tool_mesajlari[0]["content"], (
        "en eski araç çıktısı modele kırpılmadan gitmiş — kırpma bağlı değil"
    )
    # Son çıktı dokunulmamış olmalı. (Uzunluğu `read_file`ın kendi tavanı
    # belirliyor, kırpma değil — o yüzden işarete bakıyoruz.)
    assert "kısaltıldı" not in tool_mesajlari[-1]["content"], "son çıktı tam gitmeli"
