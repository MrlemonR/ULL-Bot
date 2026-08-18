"""Kısır döngü koruması (Faz 9).

Canlı testte gerçekten yaşandı: zayıf bir model `web_search`i BOŞ sorguyla
çağırdı, güvenlik katmanı "reddedildi" dedi, model **aynı çağrıyı** tekrar
yaptı — ve bu adım limiti dolana kadar sürdü. Kullanıcı dakikalarca cevap
bekledi, her adım bir model çağrısı olduğu için kota da boşa gitti.

Reddedilen bir çağrı kendi başına turu durdurmuyor (durdurmamalı da —
model başka bir yol deneyebilmeli). Durduran şey, **aynı çağrının** üst
üste başarısız olması.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.llm import LLMResponse, ToolCall
from app.agent.loop import MAX_REPEATED_FAILURES, AgentLoop


class ScriptedLLM:
    """Her çağrıda sıradaki cevabı veren sahte model."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = list(responses)
        self.calls = 0
        # Her çağrıda verilen araç şeması ve mesajlar — turu toparlama
        # çağrısının araçları KAPATTIĞINI doğrulamak için.
        self.seen_tools: list = []
        self.seen_messages: list = []

    async def complete(self, messages, tools, on_token, *, model=None, provider=""):
        self.calls += 1
        self.seen_tools.append(tools)
        self.seen_messages.append([dict(m) for m in messages])
        if self.responses:
            return self.responses.pop(0)
        # Bitince son cevabı tekrarla — "model takıldı" senaryosu.
        return LLMResponse(content="", tool_calls=[_empty_search_call()], model="fake")


def _empty_search_call(index: int = 0) -> ToolCall:
    return ToolCall(id=f"call_{index}", name="web_search", arguments='{"query": ""}')


async def collect(events: list[dict]):
    async def emit(event: dict) -> None:
        events.append(event)

    return emit


@pytest.fixture
def loop_factory(workspace, monkeypatch):
    def build(llm):
        events: list[dict] = []

        async def emit(event: dict) -> None:
            events.append(event)

        agent = AgentLoop(session_id="stuck-test", llm=llm, emit=emit, task_type="tool_use")
        return agent, events

    return build


async def test_ayni_bos_cagri_tekrarlanirsa_tur_duruyor(loop_factory):
    """Asıl senaryo: model boş sorguyu sonsuza kadar tekrarlıyor."""
    llm = ScriptedLLM([])          # her seferinde aynı boş çağrı
    agent, events = loop_factory(llm)

    result = await agent.run("kulaklık araştır")

    stopped = [e for e in events if e["type"] == "stopped"]
    assert stopped, "kısır döngü durdurulmalıydı"
    assert "başarısız" in stopped[0]["message"]
    assert "web_search" in stopped[0]["message"]
    # Adım limitine (15) kadar gitmemeli — çok daha erken durmalı.
    assert agent.steps_used <= MAX_REPEATED_FAILURES + 1, (
        f"{agent.steps_used} adım sürdü; erken durmalıydı"
    )
    assert "durduruldu" in result


async def test_esik_altinda_durmuyor(loop_factory):
    """İki başarısızlık sonra düzelen bir model engellenmemeli."""
    llm = ScriptedLLM([
        LLMResponse(content="", tool_calls=[_empty_search_call(0)], model="fake"),
        LLMResponse(content="", tool_calls=[_empty_search_call(1)], model="fake"),
        LLMResponse(content="Tamam, düzelttim.", tool_calls=[], model="fake"),
    ])
    agent, events = loop_factory(llm)

    result = await agent.run("kulaklık araştır")

    assert not [e for e in events if e["type"] == "stopped"]
    assert [e for e in events if e["type"] == "done"]
    assert result == "Tamam, düzelttim."


async def test_farkli_cagrilar_sayaci_ayri_tutuyor(loop_factory):
    """Farklı argümanlarla denemek 'takılmak' değil — model yol arıyor."""
    calls = [
        ToolCall(id="a", name="web_search", arguments='{"query": "bir"}'),
        ToolCall(id="b", name="web_search", arguments='{"query": "iki"}'),
        ToolCall(id="c", name="web_search", arguments='{"query": "üç"}'),
    ]
    llm = ScriptedLLM([
        LLMResponse(content="", tool_calls=[call], model="fake") for call in calls
    ] + [LLMResponse(content="Bitti.", tool_calls=[], model="fake")])
    agent, events = loop_factory(llm)

    # Aramalar ağa çıkmasın; hepsi başarısız olsun ama ARGÜMANLARI farklı.
    import app.web.search as search_module

    def boom(*args, **kwargs):
        raise search_module.SearchError("ağ yok")

    import app.agent.tools.web as web_tools

    original = web_tools.search
    web_tools.search = boom
    try:
        result = await agent.run("araştır")
    finally:
        web_tools.search = original

    assert not [e for e in events if e["type"] == "stopped"], (
        "farklı sorgular kısır döngü sayılmamalı"
    )
    assert result == "Bitti."


def test_imza_argumanlari_iceriyor():
    """Sayaç araç ADINA değil, ad+argüman imzasına bakmalı."""
    agent = AgentLoop(session_id="x", llm=ScriptedLLM([]))
    agent._record_failure(ToolCall(id="1", name="t", arguments=""), {"q": "a"})
    agent._record_failure(ToolCall(id="2", name="t", arguments=""), {"q": "b"})
    assert agent._stuck_on() is None, "farklı argümanlar ayrı sayılmalı"

    for _ in range(MAX_REPEATED_FAILURES):
        agent._record_failure(ToolCall(id="3", name="t", arguments=""), {"q": "aynı"})
    assert agent._stuck_on() is not None


def test_json_e_cevrilemeyen_arguman_patlatmiyor():
    """Sayaç, bozuk argümanlarda da çalışmalı — asıl döngü sebebi o."""
    agent = AgentLoop(session_id="x", llm=ScriptedLLM([]))
    weird = object()
    for _ in range(MAX_REPEATED_FAILURES):
        agent._record_failure(ToolCall(id="1", name="t", arguments=""), weird)
    assert agent._stuck_on() is not None


# --- turu toparlama: kesilse bile eldeki veriyle cevap ---------------------


class WrapUpLLM(ScriptedLLM):
    """Araçsız çağrıldığında (turu toparlama) gerçek bir cevap yazan model.

    Araçlar açıkken takılmayı sürdürüyor — canlı senaryonun aynısı: küçük
    model döngüye giriyor ama eldeki veriyle cevap yazması istendiğinde
    yazabiliyor.
    """

    def __init__(self, final_text: str = "| Model | Fiyat |\n|---|---|\n| G435 | 2.400 TL |"):
        super().__init__([])
        self.final_text = final_text

    async def complete(self, messages, tools, on_token, *, model=None, provider=""):
        if not tools:
            self.calls += 1
            self.seen_tools.append(tools)
            self.seen_messages.append([dict(m) for m in messages])
            await on_token(self.final_text)
            return LLMResponse(content=self.final_text, tool_calls=[], model="fake")
        return await super().complete(messages, tools, on_token, model=model, provider=provider)


async def test_kesilen_tur_eldeki_veriyle_cevap_yaziyor(loop_factory):
    """Canlı yakalandı: araştırma başarılıydı, sonuç kullanıcıya HİÇ ulaşmadı.

    Model Razer Barracuda X, Logitech G435 ve Corsair HS55'i bulup
    fiyatlarını aratmıştı; sonra zincirin sonundaki küçük model aynı sorguyu
    tekrarlayınca döngü koruması turu kesti ve kullanıcı sadece "işlem
    durduruldu" gördü. Artık kesme anında araçlar kapatılıp cevap yazdırılıyor.
    """
    llm = WrapUpLLM()
    agent, events = loop_factory(llm)

    result = await agent.run("kulaklıkları karşılaştır")

    assert "G435" in result, "eldeki veriden cevap üretilmeliydi"
    assert [e for e in events if e["type"] == "done"]
    assert not [e for e in events if e["type"] == "stopped"]


async def test_toparlama_cagrisinda_arac_verilmiyor(loop_factory):
    """Araçlar açık kalsaydı model aynı döngüye geri girerdi."""
    llm = WrapUpLLM()
    agent, events = loop_factory(llm)
    await agent.run("araştır")

    assert llm.seen_tools[-1] == [], "toparlama çağrısı araçsız yapılmalı"
    assert any(tools for tools in llm.seen_tools[:-1]), "önceki adımlarda araçlar açıktı"
    son_mesaj = llm.seen_messages[-1][-1]
    assert son_mesaj["role"] == "user" and "başka araç çağırma" in son_mesaj["content"]


async def test_toparlama_bos_donerse_eski_mesaja_dusuyor(loop_factory):
    """Toparlama da başarısızsa davranış eskisinden kötü olmamalı."""
    llm = WrapUpLLM(final_text="")
    agent, events = loop_factory(llm)

    result = await agent.run("araştır")

    stopped = [e for e in events if e["type"] == "stopped"]
    assert stopped and "durduruldu" in stopped[0]["message"]
    assert "durduruldu" in result


async def test_toparlama_ikinci_zinciri_de_deniyor(loop_factory, monkeypatch):
    """`reasoning` zinciri tükendiyse `tool_use` denenmeli.

    Canlı yakalandı: openrouter ve gemini aynı saniyede 429 yedi, toparlama
    "uygun sağlayıcı kalmadı" deyip pes etti — oysa `tool_use` zincirinin
    sonundaki yerel model ayaktaydı ve toplanan veri çöpe gitti.
    """
    from app.agent import loop as loop_module

    llm = WrapUpLLM()
    agent, events = loop_factory(llm)

    denenen: list[str] = []
    gercek = agent._call_model

    async def sahte(messages, on_token, task_type="default", *, tools=True):
        if not tools:
            denenen.append(task_type)
            if task_type == "reasoning":
                raise loop_module.LLMError("Uygun sağlayıcı kalmadı — hepsi cooldown")
        return await gercek(messages, on_token, task_type, tools=tools)

    monkeypatch.setattr(agent, "_call_model", sahte)
    result = await agent.run("araştır")

    assert denenen == ["reasoning", "tool_use"], f"denenen zincirler: {denenen}"
    assert "G435" in result, "ikinci zincirle cevap üretilmeliydi"
    assert not [e for e in events if e["type"] == "stopped"]
