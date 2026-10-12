import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import coder
from app.coder import PermissionBroker, ToolError, Workspace, parse_text_tool_call, run_agent
from app.main import create_app


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("def hello():\n    return 'merhaba'\n\nprint(hello())\n", "utf-8")
    (root / "README.md").write_text("# Proje\n", "utf-8")
    (root / "node_modules" / "x").mkdir(parents=True)
    return root


# --- araçlar --------------------------------------------------------------------------------


def test_workspace_blocks_escape(proj):
    ws = Workspace(proj)
    assert ws.resolve("src/app.py") == proj / "src" / "app.py"
    for bad in ("../secret", "/etc/passwd", "src/../../x"):
        with pytest.raises(ToolError):
            ws.resolve(bad)


def test_list_read_search(proj):
    ws = Workspace(proj)
    listing = coder.tool_list_files(ws)
    assert "src/" in listing and "app.py" in listing and "node_modules" not in listing
    text = coder.tool_read_file(ws, "src/app.py")
    assert text.splitlines()[1] == "2\t    return 'merhaba'"
    assert "offset=3" in coder.tool_read_file(ws, "src/app.py", limit=2)
    assert coder.tool_search(ws, r"merhaba") == "src/app.py:2: return 'merhaba'"
    assert coder.tool_search(ws, "yok-boyle-bir-sey") == "Eşleşme yok."
    with pytest.raises(ToolError):
        coder.tool_read_file(ws, "yok.py")


def test_edit_rules(proj):
    ws = Workspace(proj)
    with pytest.raises(ToolError, match="bulunamadı"):
        coder.plan_edit(ws, "src/app.py", "yok", "x")
    (proj / "dup.txt").write_text("a\na\n", "utf-8")
    with pytest.raises(ToolError, match="2 kez"):
        coder.plan_edit(ws, "dup.txt", "a", "b")
    p, old, new = coder.plan_edit(ws, "dup.txt", "a", "b", replace_all=True)
    assert new == "b\nb\n"
    p, old, new = coder.plan_edit(ws, "src/app.py", "'merhaba'", "'selam'")
    diff = coder.make_diff(old, new, "src/app.py")
    assert "-    return 'merhaba'" in diff and "+    return 'selam'" in diff


def test_run_command(proj):
    out, code = asyncio.run(coder.tool_run_command(Workspace(proj), "echo selam && exit 3"))
    assert "selam" in out and code == 3


def test_parse_text_tool_call():
    assert parse_text_tool_call('```json\n{"name": "read_file", "arguments": {"path": "a.py"}}\n```') == [
        {"function": {"name": "read_file", "arguments": {"path": "a.py"}}}
    ]
    assert parse_text_tool_call('{"name": "bilinmeyen", "arguments": {}}') == []
    assert parse_text_tool_call("Normal bir cevap.") == []
    assert parse_text_tool_call('Uzun bir açıklama yazdım ve sonra {"name": "read_file", "arguments": {}} dedim ama asıl metin bu.') == []


def test_browse(proj):
    r = coder.browse(str(proj))
    assert r["path"] == str(proj) and "src" in r["dirs"] and "node_modules" not in r["dirs"]
    assert r["parent"] == str(proj.parent)


def test_claude_command_uses_ollama():
    cmds = coder.claude_command("http://127.0.0.1:11434", "qwen3-coder", "/home/u/proje x")
    assert "ANTHROPIC_BASE_URL=http://127.0.0.1:11434" in cmds["posix"]
    assert "ANTHROPIC_AUTH_TOKEN=ollama" in cmds["posix"]
    assert "cd '/home/u/proje x'" in cmds["posix"] and cmds["posix"].endswith("claude --model qwen3-coder")


# --- ajan döngüsü ---------------------------------------------------------------------------


def scripted(*turns):
    """Her çağrıda sıradaki Ollama cevabını (parça listesi) akıtan sahte stream_chat."""
    queue = list(turns)
    seen = []

    async def stream_chat(messages):
        seen.append(messages)
        for chunk in queue.pop(0):
            yield chunk

    return stream_chat, seen


def call(name, **args):
    return {"message": {"tool_calls": [{"function": {"name": name, "arguments": args}}]}, "done": True}


def say(text):
    return [{"message": {"content": text}}, {"done": True}]


async def collect(gen, answers=None):
    events, answers = [], list(answers or [])
    async for ev in gen:
        events.append(ev)
        if ev["type"] == "permission":
            BROKER.answer(ev["id"], answers.pop(0))
    return events


BROKER = PermissionBroker()


def agent(proj, stream_chat, mode="ask", always=None):
    stored = [{"role": "user", "content": "selamı değiştir"}]
    saved = []
    gen = run_agent(stream_chat=stream_chat, stored=stored, ws=Workspace(proj), mode=lambda: mode,
                    broker=BROKER, save=saved.append, always=always if always is not None else set())
    return gen, stored, saved


def test_agent_reads_asks_and_edits(proj):
    stream_chat, seen = scripted(
        [call("read_file", path="src/app.py")],
        [call("edit_file", path="src/app.py", old_string="'merhaba'", new_string="'selam'")],
        say("Selamı değiştirdim."),
    )
    gen, stored, saved = agent(proj, stream_chat)
    events = asyncio.run(collect(gen, ["allow"]))
    types = [e["type"] for e in events]
    assert types.count("permission") == 1  # okuma izinsiz, düzenleme izinli
    perm = next(e for e in events if e["type"] == "permission")
    assert perm["kind"] == "edit" and "+    return 'selam'" in perm["diff"]
    assert "'selam'" in (proj / "src" / "app.py").read_text("utf-8")
    assert [m["role"] for m in saved] == ["assistant", "tool", "assistant", "tool", "assistant"]
    assert saved[1]["content"].startswith("1\tdef hello")  # okuma sonucu modele gitti
    assert saved[-1]["content"] == "Selamı değiştirdim."
    # Model ikinci turda sistem mesajı + önceki araç sonucunu gördü; arayüz alanları gitmedi.
    second = seen[1]
    assert second[0]["role"] == "system" and "Project folder" in second[0]["content"]
    assert all("ui" not in m for m in second)


def test_agent_denied_edit_is_reported_to_model(proj):
    stream_chat, seen = scripted(
        [call("edit_file", path="src/app.py", old_string="'merhaba'", new_string="'x'")],
        say("Tamam, değiştirmedim."),
    )
    gen, _, saved = agent(proj, stream_chat)
    asyncio.run(collect(gen, ["deny"]))
    assert "'merhaba'" in (proj / "src" / "app.py").read_text("utf-8")
    assert "reddetti" in saved[1]["content"] and saved[1]["ui"]["denied"] is True


def test_agent_always_and_modes(proj):
    always = set()
    stream_chat, _ = scripted(
        [call("write_file", path="a.txt", content="1")],
        [call("write_file", path="b.txt", content="2")],
        say("bitti"),
    )
    gen, _, _ = agent(proj, stream_chat, always=always)
    events = asyncio.run(collect(gen, ["always"]))
    assert [e["type"] for e in events].count("permission") == 1 and always == {"edit"}
    assert (proj / "b.txt").read_text() == "2"

    # "edits" modunda düzenleme sorulmaz ama komut sorulur; "all" modunda hiçbir şey sorulmaz.
    stream_chat, _ = scripted([call("write_file", path="c.txt", content="3")], [call("run_command", command="echo hi")], say("ok"))
    gen, _, saved = agent(proj, stream_chat, mode="edits")
    events = asyncio.run(collect(gen, ["allow"]))
    assert [e["kind"] for e in events if e["type"] == "permission"] == ["bash"]
    assert "hi" in saved[3]["content"]
    stream_chat, _ = scripted([call("run_command", command="echo hepsi")], say("ok"))
    gen, _, _ = agent(proj, stream_chat, mode="all")
    assert not any(e["type"] == "permission" for e in asyncio.run(collect(gen)))


def test_agent_tool_errors_go_back_to_model(proj):
    stream_chat, seen = scripted(
        [call("read_file", path="../../etc/passwd")],
        [call("hayali_arac")],
        say("Özür dilerim."),
    )
    gen, _, saved = agent(proj, stream_chat, mode="all")
    asyncio.run(collect(gen))
    assert "dışına erişim yok" in saved[1]["content"]
    assert "Bilinmeyen araç" in saved[3]["content"]


def test_agent_text_tool_call_fallback(proj):
    stream_chat, _ = scripted(
        [{"message": {"content": '{"name": "list_files", "arguments": {}}'}}, {"done": True}],
        say("Projede src var."),
    )
    gen, _, saved = agent(proj, stream_chat)
    events = asyncio.run(collect(gen))
    assert {"type": "retract"} in events
    assert saved[0]["tool_calls"][0]["function"]["name"] == "list_files" and saved[0]["content"] == ""


# --- API ---------------------------------------------------------------------------------------


class CodeOllama:
    def __init__(self):
        self.turns = []
        self.requests = []

    def __call__(self, request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen2.5-coder:7b"}]})
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.15.0"})
        if request.url.path == "/api/chat":
            body = json.loads(request.content)
            self.requests.append(body)
            chunks = self.turns.pop(0)
            if isinstance(chunks, httpx.Response):
                return chunks
            return httpx.Response(200, content="".join(json.dumps(c) + "\n" for c in chunks).encode())
        return httpx.Response(404)


@pytest.fixture
def api(tmp_path):
    ollama = CodeOllama()
    app = create_app(data_dir=tmp_path / "data", ollama_url="http://o.test", transport=httpx.MockTransport(ollama),
                     prefab_dir=tmp_path / "prefab", dataset_dir=tmp_path / "ds")
    with TestClient(app) as c:
        yield c, ollama


def ndjson(resp):
    return [json.loads(x) for x in resp.text.splitlines() if x.strip()]


def test_api_session_flow(api, proj):
    c, ollama = api
    assert c.post("/api/code/sessions", json={"cwd": str(proj / "yok")}).status_code == 422
    sess = c.post("/api/code/sessions", json={"cwd": str(proj)}).json()
    assert sess["mode"] == "ask" and sess["title"] == "Yeni oturum"
    c.put("/api/settings", json={"code_model": "qwen2.5-coder:7b"})
    c.patch(f"/api/code/sessions/{sess['id']}", json={"mode": "all"})

    ollama.turns = [
        [call("edit_file", path="src/app.py", old_string="'merhaba'", new_string="'hey'")],
        say("Değiştirdim."),
    ]
    evs = ndjson(c.post(f"/api/code/sessions/{sess['id']}/send", json={"content": "merhabayı hey yap"}))
    types = [e["type"] for e in evs]
    assert types[0] == "start" and evs[0]["model"] == "qwen2.5-coder:7b" and types[-1] == "done"
    assert "tool_result" in types and "permission" not in types
    assert "'hey'" in (proj / "src" / "app.py").read_text("utf-8")
    req = ollama.requests[0]
    assert req["model"] == "qwen2.5-coder:7b" and {t["function"]["name"] for t in req["tools"]} >= {"edit_file", "run_command"}
    assert req["options"]["num_ctx"] == 16384

    full = c.get(f"/api/code/sessions/{sess['id']}").json()
    assert full["title"] == "merhabayı hey yap"
    assert [m["role"] for m in full["messages"]] == ["user", "assistant", "tool", "assistant"]
    assert full["messages"][2]["ui"]["diff"]
    assert c.get("/api/code/sessions").json()[0]["id"] == sess["id"]
    assert c.delete(f"/api/code/sessions/{sess['id']}").json() == {"ok": True}


def test_api_model_without_tools(api, proj):
    c, ollama = api
    sess = c.post("/api/code/sessions", json={"cwd": str(proj)}).json()
    ollama.turns = [httpx.Response(400, json={"error": "registry.ollama.ai/library/gemma2:9b does not support tools"})]
    evs = ndjson(c.post(f"/api/code/sessions/{sess['id']}/send", json={"content": "x"}))
    err = next(e for e in evs if e["type"] == "error")
    assert "araç çağırmayı (tools) desteklemiyor" in err["message"]


def test_api_permission_unknown_and_claude(api, proj):
    c, _ = api
    assert c.post("/api/code/permission", json={"id": "yok", "decision": "allow"}).status_code == 404
    info = c.get("/api/code/claude").json()
    assert info["ollama_version"] == "0.15.0" and "install" in info
    b = c.get("/api/code/browse", params={"path": str(proj)}).json()
    assert b["path"] == str(proj)
