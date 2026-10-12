"""Code modu: seçilen klasörde dosya okuyup düzenleyen, komut çalıştıran ajan.

Ollama'nın araç çağırma (tool calling) desteğiyle çalışır: model bir araç
ister, ULL-Bot çalıştırır (gerekirse kullanıcıdan izin alarak), sonucu modele
geri verir; model işi bitirene kadar döngü sürer.

Güvenlik: tüm yollar oturumun klasörüne kilitli; dışına çıkan yol reddedilir.
Dosya yazma/düzenleme ve komut çalıştırma, izin moduna göre onay ister.
"""

from __future__ import annotations

import asyncio
import difflib
import fnmatch
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Callable

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".train-venv", ".mypy_cache", ".pytest_cache",
             ".ruff_cache", "dist", "build", ".next", ".idea", ".vscode", "target", ".gradle"}
MAX_READ_LINES = 400
MAX_TOOL_CHARS = 12_000      # modele giden araç çıktısı
MAX_DISPLAY_CHARS = 20_000   # arayüzde gösterilen
MAX_STEPS = 30               # bir kullanıcı mesajı başına en fazla araç turu

# izin modları: ask (her düzenleme/komutta sor), edits (düzenlemeler serbest, komutlar sorulur), all (hepsi serbest)
PERMISSION_KIND = {"write_file": "edit", "edit_file": "edit", "run_command": "bash"}


def _fn(name: str, desc: str, props: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required},
    }}


TOOLS = [
    _fn("list_files", "List files and folders (recursive, up to `depth` levels). Use first to understand the project.",
        {"path": {"type": "string", "description": "Folder relative to the project root. Default: ."},
         "depth": {"type": "integer", "description": "How many levels deep (1-4). Default: 2"}}, []),
    _fn("read_file", "Read a text file. Returns lines prefixed with line numbers.",
        {"path": {"type": "string"},
         "offset": {"type": "integer", "description": "First line to read (1-based). Default: 1"},
         "limit": {"type": "integer", "description": f"Number of lines. Default/max: {MAX_READ_LINES}"}}, ["path"]),
    _fn("search", "Search file contents with a regular expression. Returns file:line: text matches.",
        {"pattern": {"type": "string", "description": "Python regular expression"},
         "path": {"type": "string", "description": "Folder or file to search in. Default: ."},
         "glob": {"type": "string", "description": "Only files matching this pattern, e.g. *.py"}}, ["pattern"]),
    _fn("edit_file", "Replace an exact piece of text in a file. old_string must match the file exactly "
        "(including indentation, without line-number prefixes) and be unique unless replace_all is true.",
        {"path": {"type": "string"}, "old_string": {"type": "string"}, "new_string": {"type": "string"},
         "replace_all": {"type": "boolean"}}, ["path", "old_string", "new_string"]),
    _fn("write_file", "Create a new file or overwrite an existing file with the full content.",
        {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
    _fn("run_command", "Run a shell command in the project folder and return its output (e.g. tests, build, git status). "
        "Do not start long-running servers.",
        {"command": {"type": "string"},
         "timeout": {"type": "integer", "description": "Seconds, default 120"}}, ["command"]),
]
TOOL_NAMES = {t["function"]["name"] for t in TOOLS}


class ToolError(Exception):
    pass


# --- çalışma alanı ve araçlar -------------------------------------------------------------


class Workspace:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()

    def resolve(self, path: str | None) -> Path:
        p = Path(path or ".").expanduser()
        full = (p if p.is_absolute() else self.root / p).resolve()
        if full != self.root and self.root not in full.parents:
            raise ToolError(f"Proje klasörünün dışına erişim yok: {path}")
        return full

    def rel(self, p: Path) -> str:
        r = p.relative_to(self.root).as_posix()
        return r or "."


def _is_text(p: Path) -> bool:
    try:
        with open(p, "rb") as f:
            return b"\0" not in f.read(4096)
    except OSError:
        return False


def tool_list_files(ws: Workspace, path: str = ".", depth: int = 2) -> str:
    base = ws.resolve(path)
    if not base.is_dir():
        raise ToolError(f"Klasör değil: {path}")
    depth = max(1, min(int(depth or 2), 4))
    lines, count = [], 0

    def walk(d: Path, level: int, prefix: str) -> None:
        nonlocal count
        try:
            entries = sorted(d.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except OSError:
            return
        for e in entries:
            if count >= 400:
                return
            if e.name in SKIP_DIRS or (e.name.startswith(".") and e.name not in (".env.example", ".gitignore")):
                continue
            count += 1
            if e.is_dir():
                lines.append(f"{prefix}{e.name}/")
                if level < depth:
                    walk(e, level + 1, prefix + "  ")
            else:
                try:
                    size = e.stat().st_size
                except OSError:
                    size = 0
                lines.append(f"{prefix}{e.name}  ({size} B)")

    walk(base, 1, "")
    if count >= 400:
        lines.append("… (liste kısaltıldı)")
    return f"{ws.rel(base)}/\n" + ("\n".join(lines) if lines else "(boş)")


def tool_read_file(ws: Workspace, path: str, offset: int = 1, limit: int = MAX_READ_LINES) -> str:
    p = ws.resolve(path)
    if not p.is_file():
        raise ToolError(f"Dosya bulunamadı: {path}")
    if not _is_text(p):
        raise ToolError(f"İkili (metin olmayan) dosya: {path}")
    text = p.read_text("utf-8", errors="replace")
    lines = text.splitlines()
    offset = max(1, int(offset or 1))
    limit = max(1, min(int(limit or MAX_READ_LINES), MAX_READ_LINES))
    chunk = lines[offset - 1: offset - 1 + limit]
    width = len(str(offset + len(chunk)))
    body = "\n".join(f"{str(i).rjust(width)}\t{ln}" for i, ln in enumerate(chunk, offset))
    more = len(lines) - (offset - 1 + len(chunk))
    tail = f"\n… ({more} satır daha; offset={offset + len(chunk)} ile devam et)" if more > 0 else ""
    return (body or "(boş dosya)") + tail


def tool_search(ws: Workspace, pattern: str, path: str = ".", glob: str | None = None) -> str:
    try:
        rx = re.compile(pattern)
    except re.error as e:
        raise ToolError(f"Geçersiz düzenli ifade: {e}")
    base = ws.resolve(path)
    files = [base] if base.is_file() else []
    if base.is_dir():
        for root, dirs, names in os.walk(base):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for n in names:
                if glob and not fnmatch.fnmatch(n, glob):
                    continue
                files.append(Path(root) / n)
    out = []
    for f in files:
        if len(out) >= 100 or not _is_text(f):
            continue
        try:
            for i, ln in enumerate(f.read_text("utf-8", errors="replace").splitlines(), 1):
                if rx.search(ln):
                    out.append(f"{ws.rel(f)}:{i}: {ln.strip()[:200]}")
                    if len(out) >= 100:
                        break
        except OSError:
            continue
    if not out:
        return "Eşleşme yok."
    return "\n".join(out) + ("\n… (ilk 100 eşleşme)" if len(out) >= 100 else "")


def make_diff(old: str, new: str, name: str) -> str:
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True), f"a/{name}", f"b/{name}", n=3,
    ))


def plan_edit(ws: Workspace, path: str, old_string: str, new_string: str, replace_all: bool = False) -> tuple[Path, str, str]:
    """Düzenlemeyi uygulamadan hesapla: (yol, eski içerik, yeni içerik)."""
    p = ws.resolve(path)
    if not p.is_file():
        raise ToolError(f"Dosya bulunamadı: {path}")
    old = p.read_text("utf-8", errors="replace")
    if not old_string:
        raise ToolError("old_string boş olamaz (yeni dosya için write_file kullan)")
    n = old.count(old_string)
    if n == 0:
        raise ToolError("old_string dosyada bulunamadı. Önce read_file ile güncel içeriği oku; boşlukları ve girintiyi aynen kopyala.")
    if n > 1 and not replace_all:
        raise ToolError(f"old_string dosyada {n} kez geçiyor; daha fazla çevre satırı ekleyerek tekil yap ya da replace_all=true ver.")
    new = old.replace(old_string, new_string) if replace_all else old.replace(old_string, new_string, 1)
    return p, old, new


def plan_write(ws: Workspace, path: str, content: str) -> tuple[Path, str, str]:
    p = ws.resolve(path)
    if p.is_dir():
        raise ToolError(f"Bu bir klasör: {path}")
    old = p.read_text("utf-8", errors="replace") if p.is_file() else ""
    return p, old, content


def apply_change(p: Path, new: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(new, "utf-8")


async def tool_run_command(ws: Workspace, command: str, timeout: int = 120) -> tuple[str, int]:
    timeout = max(1, min(int(timeout or 120), 600))
    kwargs: dict[str, Any] = {}
    if os.name != "nt":
        kwargs["start_new_session"] = True  # iptal edilince tüm alt süreçleri birlikte öldürebilmek için
    proc = await asyncio.create_subprocess_shell(
        command, cwd=str(ws.root), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.DEVNULL, **kwargs,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        code = proc.returncode or 0
        text = out.decode("utf-8", "replace")
    except asyncio.TimeoutError:
        _kill(proc)
        text, code = f"(zaman aşımı: {timeout} sn sonra durduruldu)", 124
    except asyncio.CancelledError:
        _kill(proc)
        raise
    return text, code


def _kill(proc) -> None:
    try:
        if os.name != "nt":
            os.killpg(proc.pid, 9)
        else:
            proc.kill()
    except (ProcessLookupError, OSError):
        pass


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + f"\n… ({len(text) - limit} karakter kısaltıldı) …\n" + text[-half:]


# --- izin aracısı --------------------------------------------------------------------------


class PermissionBroker:
    """Ajanın "izin ver?" sorusunu tarayıcının cevabına bağlar."""

    def __init__(self):
        self._pending: dict[str, asyncio.Future] = {}

    def ask(self) -> tuple[str, asyncio.Future]:
        rid = uuid.uuid4().hex[:12]
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        return rid, fut

    def answer(self, rid: str, decision: str) -> bool:
        fut = self._pending.pop(rid, None)
        if fut is None or fut.done():
            return False
        fut.get_loop().call_soon_threadsafe(fut.set_result, decision)
        return True

    def drop(self, rid: str) -> None:
        fut = self._pending.pop(rid, None)
        if fut and not fut.done():
            fut.cancel()


# --- ajan döngüsü ---------------------------------------------------------------------------


def system_prompt(ws: Workspace) -> str:
    try:
        listing = tool_list_files(ws, ".", 1)
    except ToolError:
        listing = "(okunamadı)"
    return (
        "You are ULL-Bot Code, an autonomous coding agent running on the user's computer.\n"
        f"Project folder: {ws.root}\nOS: {platform.system()} {platform.release()}\n\n"
        "You have tools: list_files, read_file, search, edit_file, write_file, run_command. "
        "Paths are relative to the project folder.\n"
        "Rules:\n"
        "- Explore before changing: list, search and read the relevant files first. Never guess file contents.\n"
        "- For changes prefer edit_file with an exact old_string copied from read_file output "
        "(without the line-number prefix), keeping indentation. Use write_file for new files.\n"
        "- Make minimal, focused changes that do what the user asked. Keep the existing code style.\n"
        "- Use run_command to run tests or builds when useful. Do not start servers that never exit.\n"
        "- If a tool returns an error, read it and fix your call instead of repeating it.\n"
        "- When done, give a short summary of what you changed. Reply in the user's language.\n\n"
        f"Top-level contents:\n{listing}"
    )


JSON_CALL = re.compile(r"\{[\s\S]*\}")


def parse_text_tool_call(content: str) -> list[dict]:
    """Bazı küçük modeller aracı tool_calls yerine metin olarak JSON yazar; onu yakala."""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?|```$", "", text.strip()).strip()
    m = JSON_CALL.search(text)
    if not m or len(text) - len(m.group(0)) > 40:
        return []
    try:
        obj = json.loads(m.group(0))
    except ValueError:
        return []
    items = obj if isinstance(obj, list) else [obj]
    calls = []
    for it in items:
        if not isinstance(it, dict):
            return []
        name = it.get("name") or (it.get("function") or {}).get("name")
        args = it.get("arguments") or it.get("parameters") or (it.get("function") or {}).get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                return []
        if name not in TOOL_NAMES or not isinstance(args, dict):
            return []
        calls.append({"function": {"name": name, "arguments": args}})
    return calls


def model_messages(stored: list[dict]) -> list[dict]:
    """Saklanan mesajlardan Ollama'ya gidecek liste (arayüz alanları atılır; eski araç çıktıları kısaltılır)."""
    out = []
    n = len(stored)
    for i, m in enumerate(stored):
        msg = {k: v for k, v in m.items() if k in ("role", "content", "tool_calls", "tool_name")}
        if msg["role"] == "tool" and i < n - 12:
            msg["content"] = clip(msg.get("content", ""), 1500)
        out.append(msg)
    return out


def describe_call(name: str, args: dict) -> str:
    if name == "run_command":
        return str(args.get("command", ""))
    if name in ("read_file", "edit_file", "write_file"):
        return str(args.get("path", ""))
    if name == "list_files":
        return str(args.get("path") or ".")
    if name == "search":
        return f"{args.get('pattern', '')}" + (f" · {args.get('glob')}" if args.get("glob") else "")
    return json.dumps(args, ensure_ascii=False)[:120]


async def run_agent(
    *,
    stream_chat: Callable[[list[dict]], AsyncIterator[dict]],
    stored: list[dict],
    ws: Workspace,
    mode: Callable[[], str],
    broker: PermissionBroker,
    save: Callable[[dict], None],
    always: set[str],
) -> AsyncIterator[dict]:
    """Bir kullanıcı mesajının cevabını üretir; olayları yield eder.

    stream_chat(messages) → Ollama'nın /api/chat akış parçaları.
    stored → oturumun tüm mesajları (yeni kullanıcı mesajı dahil); yeni mesajlar save() ile eklenir.
    always → bu oturumda "hep izin ver" denmiş izin türleri (edit / bash).
    mode() → güncel izin modu (çalışırken değiştirilebilir).
    """
    for _ in range(MAX_STEPS):
        content, thinking, calls = [], [], []
        try:
            async for chunk in stream_chat([{"role": "system", "content": system_prompt(ws)}, *model_messages(stored)]):
                if chunk.get("error"):
                    yield {"type": "error", "message": str(chunk["error"])}
                    return
                msg = chunk.get("message") or {}
                if msg.get("thinking"):
                    thinking.append(msg["thinking"])
                    yield {"type": "thinking", "content": msg["thinking"]}
                if msg.get("content"):
                    content.append(msg["content"])
                    yield {"type": "token", "content": msg["content"]}
                for c in msg.get("tool_calls") or []:
                    calls.append(c)
        except (asyncio.CancelledError, GeneratorExit):
            # Kullanıcı durdurdu: yarım cevabı kaybetme.
            if content:
                partial = {"role": "assistant", "content": "".join(content), "stopped": True}
                stored.append(partial)
                save(partial)
            raise
        text = "".join(content)
        if not calls:
            calls = parse_text_tool_call(text)
            if calls:
                yield {"type": "retract"}  # metin olarak gelen araç çağrısını ekrandan sil
                text = ""

        assistant = {"role": "assistant", "content": text}
        if calls:
            assistant["tool_calls"] = [{"function": {"name": c["function"]["name"], "arguments": c["function"].get("arguments") or {}}} for c in calls]
        if thinking:
            assistant["thinking"] = "".join(thinking)
        stored.append(assistant)
        save(assistant)
        yield {"type": "turn_end"}
        if not calls:
            return

        for c in assistant["tool_calls"]:
            name = c["function"]["name"]
            args = c["function"]["arguments"]
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            call_id = uuid.uuid4().hex[:10]
            yield {"type": "tool_call", "id": call_id, "name": name, "args": args, "label": describe_call(name, args)}
            ui: dict[str, Any] = {"call_id": call_id, "name": name, "args": args, "label": describe_call(name, args)}
            try:
                result = None
                if name not in TOOL_NAMES:
                    raise ToolError(f"Bilinmeyen araç: {name}. Kullanılabilir: {', '.join(sorted(TOOL_NAMES))}")
                kind = PERMISSION_KIND.get(name)
                change = None
                if name == "edit_file":
                    change = plan_edit(ws, args.get("path", ""), args.get("old_string", ""), args.get("new_string", ""), bool(args.get("replace_all")))
                elif name == "write_file":
                    change = plan_write(ws, args.get("path", ""), args.get("content", ""))
                if change:
                    ui["diff"] = make_diff(change[1], change[2], ws.rel(change[0]))
                    ui["new_file"] = not change[0].exists()

                current = mode()
                allowed = (
                    kind is None or current == "all" or kind in always or (kind == "edit" and current == "edits")
                )
                if not allowed:
                    rid, fut = broker.ask()
                    yield {"type": "permission", "id": rid, "call_id": call_id, "kind": kind, "name": name,
                           "label": ui["label"], "diff": ui.get("diff"), "new_file": ui.get("new_file", False)}
                    try:
                        decision = await fut
                    finally:
                        broker.drop(rid)
                    if decision == "always":
                        always.add(kind)
                    elif decision != "allow":
                        raise ToolError("Kullanıcı bu işlemi reddetti. Ne yapmak istediğini sor ya da başka bir yol dene.")

                if name == "list_files":
                    result = tool_list_files(ws, args.get("path") or ".", args.get("depth") or 2)
                elif name == "read_file":
                    result = tool_read_file(ws, args.get("path", ""), args.get("offset") or 1, args.get("limit") or MAX_READ_LINES)
                elif name == "search":
                    result = tool_search(ws, args.get("pattern", ""), args.get("path") or ".", args.get("glob"))
                elif change:
                    apply_change(change[0], change[2])
                    added = sum(1 for ln in ui["diff"].splitlines() if ln.startswith("+") and not ln.startswith("+++"))
                    removed = sum(1 for ln in ui["diff"].splitlines() if ln.startswith("-") and not ln.startswith("---"))
                    result = f"{'Oluşturuldu' if ui.get('new_file') else 'Güncellendi'}: {ws.rel(change[0])} (+{added} −{removed})"
                elif name == "run_command":
                    yield {"type": "tool_running", "id": call_id}
                    out, code = await tool_run_command(ws, args.get("command", ""), args.get("timeout") or 120)
                    ui["exit_code"] = code
                    result = f"exit code {code}\n{out}"
                ui["ok"] = True
            except ToolError as e:
                result = f"HATA: {e}"
                ui["ok"] = False
                ui["denied"] = "reddetti" in str(e)
            except (OSError, ValueError) as e:
                result = f"HATA: {e}"
                ui["ok"] = False
            ui["display"] = clip(result, MAX_DISPLAY_CHARS)
            tool_msg = {"role": "tool", "content": clip(result, MAX_TOOL_CHARS), "tool_name": name, "ui": ui}
            stored.append(tool_msg)
            save(tool_msg)
            yield {"type": "tool_result", **ui}
    yield {"type": "error", "message": f"{MAX_STEPS} araç turuna ulaşıldı; durduruldu. Devam etmesi için yeni mesaj yaz."}


# --- klasör seçici ---------------------------------------------------------------------------


def browse(path: str | None) -> dict:
    p = Path(path or Path.home()).expanduser()
    try:
        p = p.resolve()
    except OSError:
        p = Path.home()
    if not p.is_dir():
        p = p.parent if p.parent.is_dir() else Path.home()
    dirs = []
    try:
        for e in sorted(p.iterdir(), key=lambda e: e.name.lower()):
            if e.is_dir() and not e.name.startswith(".") and e.name not in SKIP_DIRS:
                dirs.append(e.name)
    except OSError:
        pass
    markers = [m for m in (".git", "package.json", "pyproject.toml", "Cargo.toml", "go.mod", "pom.xml", "*.sln")
               if (list(p.glob(m)) if "*" in m else (p / m).exists())]
    return {"path": str(p), "parent": str(p.parent) if p.parent != p else None, "dirs": dirs[:500], "markers": markers}


# --- Claude Code başlatıcı ------------------------------------------------------------------------


def claude_env(ollama_url: str) -> dict[str, str]:
    # Ollama (0.14+) Anthropic Messages API'sini taklit ediyor; Claude Code bu adrese bağlanır.
    return {"ANTHROPIC_BASE_URL": ollama_url, "ANTHROPIC_AUTH_TOKEN": "ollama", "ANTHROPIC_API_KEY": ""}


def claude_command(ollama_url: str, model: str, cwd: str) -> dict[str, str]:
    env = claude_env(ollama_url)
    posix = " ".join(f"{k}={shlex.quote(v) if v else chr(34) * 2}" for k, v in env.items())
    win = "; ".join(f"$env:{k}='{v}'" for k, v in env.items())
    return {
        "posix": f"cd {shlex.quote(cwd)} && {posix} claude --model {shlex.quote(model)}",
        "powershell": f"cd '{cwd}'; {win}; claude --model {model}",
    }


INSTALL_HINT = {
    "posix": "curl -fsSL https://claude.ai/install.sh | bash",
    "windows": "irm https://claude.ai/install.ps1 | iex",
    "npm": "npm install -g @anthropic-ai/claude-code",
}


def launch_claude(cwd: str, model: str, ollama_url: str) -> dict:
    """Claude Code'u sistem terminalinde, klasörde ve yerel modelle açar."""
    cmds = claude_command(ollama_url, model, cwd)
    claude = shutil.which("claude")
    result = {"commands": cmds, "install": INSTALL_HINT, "found": bool(claude)}
    if not claude:
        result["error"] = "`claude` komutu bulunamadı. Önce Claude Code'u kur (aşağıdaki komut), sonra tekrar dene."
        return result
    env = claude_env(ollama_url)
    try:
        if sys.platform.startswith("win"):
            sets = " && ".join(f'set "{k}={v}"' for k, v in env.items())
            subprocess.Popen(f'start "ULL-Bot · Claude Code" cmd /k "{sets} && claude --model {model}"',
                             cwd=cwd, shell=True)
        elif sys.platform == "darwin":
            script = cmds["posix"].replace("\\", "\\\\").replace('"', '\\"')
            subprocess.Popen(["osascript", "-e", f'tell application "Terminal" to do script "{script}"',
                              "-e", 'tell application "Terminal" to activate'])
        else:
            exports = "; ".join(f"export {k}={shlex.quote(v) if v else chr(34) * 2}" for k, v in env.items())
            script = f"cd {shlex.quote(cwd)}; {exports}; claude --model {shlex.quote(model)}; exec bash"
            term = _linux_terminal(cwd, script)
            if not term:
                result["error"] = "Terminal programı bulunamadı; aşağıdaki komutu bir terminalde çalıştır."
                return result
            subprocess.Popen(term, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        result["error"] = f"Terminal açılamadı: {e}"
        return result
    result["ok"] = True
    return result


def _linux_terminal(cwd: str, script: str) -> list[str] | None:
    sh = ["bash", "-lc", script]
    preferred = os.environ.get("TERMINAL")
    options = [
        ("gnome-terminal", ["gnome-terminal", f"--working-directory={cwd}", "--", *sh]),
        ("konsole", ["konsole", "--workdir", cwd, "-e", *sh]),
        ("xfce4-terminal", ["xfce4-terminal", "--working-directory", cwd, "-x", *sh]),
        ("kitty", ["kitty", "--directory", cwd, *sh]),
        ("alacritty", ["alacritty", "--working-directory", cwd, "-e", *sh]),
        ("wezterm", ["wezterm", "start", "--cwd", cwd, "--", *sh]),
        ("foot", ["foot", "--working-directory", cwd, *sh]),
        ("tilix", ["tilix", "-w", cwd, "-e", " ".join(shlex.quote(x) for x in sh)]),
        ("x-terminal-emulator", ["x-terminal-emulator", "-e", *sh]),
        ("xterm", ["xterm", "-e", *sh]),
    ]
    if preferred and shutil.which(preferred):
        return [preferred, "-e", *sh]
    for exe, cmd in options:
        if shutil.which(exe):
            return cmd
    return None

