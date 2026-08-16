r"""Kabuk komutu sınıflandırma: safe / confirm / blocked (spec §6.3).

Tasarım ilkeleri:

1. **Safe için allowlist, blocked için denylist, arası hep `confirm`.**
   Tanımadığımız hiçbir komut sessizce çalışmaz. Bir kalıbı unutmuş olmamızın
   cezası "kullanıcıya sorulur" olur, "sistem bozulur" değil.
2. **Analiz edilemeyen komut = blocked.** Komut ikamesi (`$(...)`, backtick),
   alt kabuk `( ... )`, `eval`, kapanmamış tırnak: bunların içinde ne olduğunu
   güvenilir biçimde göremeyiz, o yüzden reddedilir.
3. **Obfuscation tokenizer'da çözülür.** `"su""do"` ve `s\udo` gibi yazımlar
   POSIX shlex ile tek bir `sudo` token'ına iner; kontrol tam metinde değil
   çözümlenmiş token üzerinde yapılır (spec §10).
4. **Bu liste config'e taşınmaz.** Kullanıcı UI'dan `confirm` → `safe` yapabilir
   ama `blocked` listesi kod içinde sabittir (spec §7.3).
"""

from __future__ import annotations

import shlex
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.safety.sandbox import (
    HARD_DENIED_PATHS,
    PathViolation,
    get_workspace_config,
    resolve_path,
)

Risk = Literal["safe", "confirm", "blocked"]

_RISK_ORDER: dict[Risk, int] = {"safe": 0, "confirm": 1, "blocked": 2}

MAX_COMMAND_LENGTH = 4000
MAX_UNWRAP_DEPTH = 3


@dataclass(frozen=True)
class Decision:
    risk: Risk
    reason: str
    rule: str = ""

    def worse_than(self, other: "Decision") -> bool:
        return _RISK_ORDER[self.risk] > _RISK_ORDER[other.risk]


SAFE = Decision("safe", "Salt okunur komut.", "allowlist")


def escalate(risk: Risk, to: Risk) -> Risk:
    """İki risk seviyesinden ağır olanı döndür."""
    return risk if _RISK_ORDER[risk] >= _RISK_ORDER[to] else to


# --- Blocked: hiçbir koşulda çalışmaz -------------------------------------

_PRIVILEGE = {"sudo", "su", "doas", "pkexec", "runuser", "sudoedit"}
_PACKAGES = {
    "pacman", "yay", "paru", "pamac", "makepkg", "pacman-key",
    "apt", "apt-get", "aptitude", "dpkg", "rpm", "dnf", "yum", "zypper",
    "snap", "flatpak", "emerge", "xbps-install", "eopkg",
}
_SERVICES = {
    "systemctl", "systemd-run", "rc-service", "rc-update", "service",
    "initctl", "telinit", "init", "openrc", "sv", "s6-svc",
}
_POWER = {"reboot", "shutdown", "poweroff", "halt", "kexec"}
_DISK = {
    "fdisk", "cfdisk", "sfdisk", "parted", "gparted", "mkswap", "swapon",
    "swapoff", "wipefs", "blkdiscard", "shred", "cryptsetup", "losetup",
    "badblocks", "hdparm", "dd", "mkinitcpio", "grub-install",
    "grub-mkconfig", "efibootmgr", "bootctl", "mount", "umount",
}
_KERNEL_NET = {
    "modprobe", "insmod", "rmmod", "depmod", "sysctl",
    "iptables", "ip6tables", "nft", "ufw", "firewall-cmd",
}
_ACCOUNTS = {
    "useradd", "userdel", "usermod", "groupadd", "groupdel", "gpasswd",
    "passwd", "chpasswd", "visudo", "chsh", "chroot", "nsenter", "unshare",
}
# Analiz kaçırma primitifleri: içeriği çalışma anında oluşuyor.
_EVASION = {"eval", "source", "exec", "crontab", "at", "batch"}

BLOCKED_EXECUTABLES: frozenset[str] = frozenset(
    _PRIVILEGE | _PACKAGES | _SERVICES | _POWER | _DISK | _KERNEL_NET | _ACCOUNTS | _EVASION
)

# `mkfs`, `mkfs.ext4`, `mkfs.btrfs` ...
BLOCKED_PREFIXES: tuple[str, ...] = ("mkfs",)

_SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish", "csh", "tcsh", "ash", "busybox"}
_INTERPRETERS = _SHELLS | {
    "python", "python2", "python3", "perl", "ruby", "node", "nodejs",
    "php", "lua", "deno", "bun",
}

# Komutu sarmalayıp başka bir komut çalıştıranlar: iç komut ayrıca sınıflandırılır
# (`env sudo rm -rf /` deliğini kapatır).
_WRAPPERS: dict[str, int] = {
    # ad -> sarmalayıcının kendi konumsal argüman sayısı (komuttan önce)
    "env": 0, "nohup": 0, "setsid": 0, "stdbuf": 0, "time": 0,
    "nice": 0, "ionice": 0, "xargs": 0, "watch": 0, "doas": 0,
    "timeout": 1,  # timeout 5 <komut>
}

# --- Safe: sorulmadan çalışır ---------------------------------------------
# Değer `None` ise tüm alt komutlar serbest, set ise sadece o alt komutlar.
SAFE_COMMANDS: dict[str, frozenset[str] | None] = {
    "ls": None, "dir": None, "tree": None, "pwd": None, "stat": None,
    "file": None, "du": None, "df": None, "wc": None, "basename": None,
    "dirname": None, "readlink": None, "realpath": None,
    "cat": None, "bat": None, "head": None, "tail": None, "less": None,
    "nl": None, "sort": None, "uniq": None, "cut": None, "column": None,
    "grep": None, "egrep": None, "fgrep": None, "rg": None, "ag": None,
    "find": None, "fd": None, "locate": None, "which": None, "whereis": None,
    "type": None, "command": None,
    "echo": None, "printf": None, "date": None, "cal": None, "uname": None,
    "whoami": None, "id": None, "hostname": None, "uptime": None,
    "free": None, "lscpu": None, "lsblk": None, "nproc": None,
    # NOT: `env` bilinçli olarak burada yok — hem sarmalayıcı (`env sudo ...`)
    # hem de API anahtarlarını ekrana döken bir komut. Her ikisi de `confirm`.
    "diff": None, "cmp": None, "md5sum": None, "sha256sum": None,
    "jq": None, "yq": None, "xxd": None, "strings": None,
    "git": frozenset({
        "status", "log", "diff", "show", "branch", "remote", "describe",
        "rev-parse", "ls-files", "ls-tree", "blame", "shortlog", "tag",
        "config", "stash", "worktree", "cat-file", "reflog",
    }),
}

# `find` bu bayraklarla artık salt okunur değil.
_FIND_UNSAFE_FLAGS = {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprintf", "-fls", "-fprint"}
# `git config --global` yazma işlemidir; `stash drop`/`worktree remove` de öyle.
_GIT_WRITE_HINTS = {"--global", "--system", "--unset", "--add", "--replace-all"}

_REDIRECT_TOKENS = {">", ">>", "<", "<<", "<<<", "&>", ">&", "2>", "2>>"}
_SEPARATORS = {";", "&&", "||", "|", "&", "|&", "\n"}
_GROUPING = {"(", ")", "{", "}", "((", "))", "()"}

_GLOB_CHARS = set("*?[")


def _basename(token: str) -> str:
    return Path(token).name


def _is_assignment(token: str) -> bool:
    """`FOO=bar ls` biçimindeki ön-atama."""
    if "=" not in token:
        return False
    name = token.split("=", 1)[0]
    return bool(name) and (name[0].isalpha() or name[0] == "_") and all(
        ch.isalnum() or ch == "_" for ch in name
    )


def _structural_reject(command: str) -> Decision | None:
    """Tokenize etmeden önce: içeriği çalışma anında oluşan kalıpları ele."""
    if not command.strip():
        return Decision("blocked", "Boş komut.", "empty")
    if len(command) > MAX_COMMAND_LENGTH:
        return Decision("blocked", f"Komut {MAX_COMMAND_LENGTH} karakteri aşıyor.", "too-long")
    if "\x00" in command:
        return Decision("blocked", "Komut null byte içeriyor.", "null-byte")

    for ch in command:
        if ch in "\t\n":
            continue
        if unicodedata.category(ch) in {"Cc", "Cf"}:
            return Decision("blocked", "Komut kontrol karakteri içeriyor.", "control-char")

    patterns = {
        "$(": "komut ikamesi $( )",
        "`": "komut ikamesi (backtick)",
        "${": "değişken genişletme ${ }",
        "$'": "ANSI-C tırnak $'...'",
        "<(": "süreç ikamesi <( )",
        ">(": "süreç ikamesi >( )",
    }
    for pattern, label in patterns.items():
        if pattern in command:
            return Decision(
                "blocked",
                f"Komut {label} içeriyor; ne çalıştıracağı statik olarak görülemiyor.",
                "substitution",
            )
    return None


def _tokenize(command: str) -> list[str] | Decision:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError as exc:
        return Decision("blocked", f"Komut ayrıştırılamadı ({exc}).", "parse-error")


def _split_segments(tokens: list[str]) -> list[list[str]] | Decision:
    """Token listesini `;`, `&&`, `|` gibi ayraçlardan bölerek komutlara ayır."""
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in _GROUPING:
            return Decision(
                "blocked",
                "Komut alt kabuk/grup ifadesi içeriyor; güvenli biçimde çözümlenemiyor.",
                "grouping",
            )
        if token in _SEPARATORS:
            segments.append(current)
            current = []
            continue
        current.append(token)
    segments.append(current)
    return [seg for seg in segments if seg]


def _split_redirects(segment: list[str]) -> tuple[list[str], list[str]]:
    """Segmenti (komut token'ları, yönlendirme hedefleri) olarak ayır."""
    command_tokens: list[str] = []
    targets: list[str] = []
    index = 0
    while index < len(segment):
        token = segment[index]
        if token in _REDIRECT_TOKENS or (">" in token and token.rstrip("0123456789") in _REDIRECT_TOKENS):
            if index + 1 < len(segment):
                targets.append(segment[index + 1])
                index += 2
                continue
            index += 1
            continue
        command_tokens.append(token)
        index += 1
    return command_tokens, targets


def _executable_of(segment: list[str]) -> tuple[str | None, list[str]]:
    """Ön-atamaları atlayarak (çalıştırılabilir, argümanlar) döndür."""
    index = 0
    while index < len(segment) and _is_assignment(segment[index]):
        index += 1
    if index >= len(segment):
        return None, []
    return segment[index], segment[index + 1 :]


def _resolvable_name(token: str) -> bool:
    """Çalıştırılabilir adı statik olarak okunabiliyor mu?

    `$X`, `s*do`, ASCII dışı homoglyph vb. isimler çözümlenemez sayılır.
    """
    if not token:
        return False
    if any(ord(ch) > 127 for ch in token):
        return False
    return all(ch.isalnum() or ch in "._/+-:@" for ch in token)


def _is_path_like(arg: str, cwd: Path) -> bool:
    if arg.startswith("-"):
        return False
    if "$" in arg:
        # `cat $SECRET_PATH` — neyi okuyacağı belli değil, doğrulanamaz sayılır.
        return True
    if "/" in arg or arg.startswith("~"):
        return True
    try:
        return (cwd / arg).exists()
    except OSError:  # pragma: no cover
        return False


def _glob_free_prefix(arg: str) -> str:
    """Glob içeren yolun sabit dizin kısmını al: `/etc/*` -> `/etc`."""
    if not (_GLOB_CHARS & set(arg)):
        return arg
    parts = arg.split("/")
    keep: list[str] = []
    for part in parts:
        if _GLOB_CHARS & set(part):
            break
        keep.append(part)
    return "/".join(keep) or "."


def _check_path_argument(arg: str, cwd: Path) -> Decision | None:
    """Salt okunur bir komutun yol argümanını çalışma alanına karşı doğrula."""
    if "$" in arg:
        return Decision(
            "confirm",
            f"'{arg}' çalışma anında genişliyor, hedefi önceden doğrulanamıyor.",
            "unverifiable-arg",
        )
    target = _glob_free_prefix(arg)
    try:
        resolve_path(target, base=cwd)
    except PathViolation as exc:
        resolved = Path(target).expanduser()
        if not resolved.is_absolute():
            resolved = cwd / resolved
        try:
            resolved = resolved.resolve()
        except OSError:  # pragma: no cover
            pass
        for denied in HARD_DENIED_PATHS:
            if resolved == denied or resolved.is_relative_to(denied):
                return Decision("blocked", f"Sistem dizinine erişim: {resolved}", "denied-path")
        for denied in get_workspace_config().denied_paths:
            if resolved == denied or resolved.is_relative_to(denied):
                return Decision("blocked", f"Yasaklı yol: {resolved}", "denied-path")
        return Decision("confirm", str(exc), "outside-workspace")
    return None


def _classify_targets(targets: list[str], cwd: Path) -> Decision:
    """Yönlendirme hedefleri: her `>` bir yazma işlemidir."""
    worst = SAFE
    for target in targets:
        decision = _check_path_argument(target, cwd)
        if decision is not None and decision.risk == "blocked":
            return Decision("blocked", f"Yasaklı hedefe yazma: {target}", "redirect")
        candidate = Decision("confirm", f"Çıktı '{target}' dosyasına yazılıyor.", "redirect")
        if candidate.worse_than(worst):
            worst = candidate
    return worst


def _blocked_executable(name: str) -> Decision | None:
    if name in BLOCKED_EXECUTABLES:
        return Decision("blocked", f"'{name}' kesin yasaklı komutlar listesinde.", f"blocked:{name}")
    for prefix in BLOCKED_PREFIXES:
        if name.startswith(prefix):
            return Decision("blocked", f"'{name}' dosya sistemi oluşturma komutu.", f"blocked:{prefix}*")
    return None


def _classify_rm(args: list[str], cwd: Path, home: Path) -> Decision:
    flags = "".join(a[1:] for a in args if a.startswith("-") and not a.startswith("--"))
    long_flags = {a for a in args if a.startswith("--")}
    recursive = "r" in flags or "R" in flags or "--recursive" in long_flags
    force = "f" in flags or "--force" in long_flags
    paths = [a for a in args if not a.startswith("-")]

    if not paths:
        return Decision("confirm", "Silme komutu (hedef belirsiz).", "rm")

    for arg in paths:
        if "$" in arg:
            return Decision(
                "blocked",
                f"'rm' hedefi '{arg}' çalışma anında genişliyor; doğrulanamaz.",
                "rm:unverifiable",
            )
        target = Path(_glob_free_prefix(arg)).expanduser()
        if not target.is_absolute():
            target = cwd / target
        try:
            target = target.resolve()
        except OSError:  # pragma: no cover
            pass

        if target == Path("/") or target == home:
            return Decision("blocked", f"'rm' hedefi {target}.", "rm:root")
        for denied in HARD_DENIED_PATHS:
            if target == denied or target.is_relative_to(denied):
                return Decision("blocked", f"'rm' sistem dizinini hedefliyor: {target}", "rm:system")
        if recursive and force and not target.is_relative_to(home):
            return Decision(
                "blocked",
                f"'rm -rf' ev dizininin dışına çıkıyor: {target}",
                "rm:outside-home",
            )
    return Decision("confirm", "Dosya silme işlemi.", "rm")


def _classify_ownership(name: str, args: list[str], cwd: Path) -> Decision:
    for arg in args:
        if arg.startswith("-"):
            continue
        target = Path(_glob_free_prefix(arg)).expanduser()
        if not target.is_absolute():
            continue  # göreli yollar çalışma alanı içinde kalır
        try:
            target = target.resolve()
        except OSError:  # pragma: no cover
            pass
        if target == Path("/"):
            return Decision("blocked", f"'{name}' kök dizini hedefliyor.", f"{name}:root")
        for denied in HARD_DENIED_PATHS:
            if target == denied or target.is_relative_to(denied):
                return Decision("blocked", f"'{name}' sistem dizinini hedefliyor: {target}", f"{name}:system")
    return Decision("confirm", f"'{name}' izin/sahiplik değiştiriyor.", name)


def _classify_segment(segment: list[str], cwd: Path, home: Path, depth: int = 0) -> Decision:
    command_tokens, targets = _split_redirects(segment)
    exe_token, args = _executable_of(command_tokens)
    if exe_token is None:
        return Decision("confirm", "Komut adı okunamadı.", "no-exe")

    if not _resolvable_name(exe_token):
        return Decision(
            "blocked",
            f"Komut adı '{exe_token}' statik olarak çözümlenemiyor.",
            "unresolvable-exe",
        )

    name = _basename(unicodedata.normalize("NFKC", exe_token))

    blocked = _blocked_executable(name)
    if blocked is not None:
        return blocked

    # Sarmalayıcı ise iç komutu ayrıca sınıflandır: `env sudo ...` yakalanmalı.
    if name in _WRAPPERS and depth < MAX_UNWRAP_DEPTH:
        skip = _WRAPPERS[name]
        inner = [a for a in args if not a.startswith("-")]
        inner = inner[skip:]
        if inner:
            inner_decision = _classify_segment(inner, cwd, home, depth + 1)
            if inner_decision.risk == "blocked":
                return inner_decision
        return Decision("confirm", f"'{name}' başka bir komutu sarmalıyor.", f"wrapper:{name}")

    redirect_decision = _classify_targets(targets, cwd)
    if redirect_decision.risk == "blocked":
        return redirect_decision

    if name == "rm":
        return max(
            (_classify_rm(args, cwd, home), redirect_decision),
            key=lambda d: _RISK_ORDER[d.risk],
        )
    if name in {"chmod", "chown", "chgrp", "setfacl", "setcap"}:
        return max(
            (_classify_ownership(name, args, cwd), redirect_decision),
            key=lambda d: _RISK_ORDER[d.risk],
        )

    if name not in SAFE_COMMANDS:
        return max(
            (Decision("confirm", f"'{name}' salt okunur allowlist'te değil.", "not-allowlisted"),
             redirect_decision),
            key=lambda d: _RISK_ORDER[d.risk],
        )

    subcommands = SAFE_COMMANDS[name]
    if subcommands is not None:
        positional = [a for a in args if not a.startswith("-")]
        sub = positional[0] if positional else ""
        if sub not in subcommands:
            return Decision("confirm", f"'{name} {sub}' salt okunur değil.", f"{name}:subcommand")
        if name == "git" and _GIT_WRITE_HINTS & set(args):
            return Decision("confirm", "'git config' yazma bayrağı içeriyor.", "git:write-flag")

    if name in {"find", "fd"} and _FIND_UNSAFE_FLAGS & set(args):
        return Decision("confirm", f"'{name}' komutu dosya değiştiren bayrak içeriyor.", "find:exec")

    # Salt okunur komut bile olsa yol argümanları çalışma alanına uymalı.
    for arg in args:
        if not _is_path_like(arg, cwd):
            continue
        decision = _check_path_argument(arg, cwd)
        if decision is not None and decision.worse_than(redirect_decision):
            redirect_decision = decision

    if redirect_decision.risk != "safe":
        return redirect_decision
    return SAFE


def classify_command(
    command: str,
    *,
    cwd: Path | None = None,
    home: Path | None = None,
) -> Decision:
    """Bir kabuk komutunu safe / confirm / blocked olarak sınıflandır."""
    home = (home or Path.home()).expanduser()
    cwd = (cwd or home).expanduser()

    rejection = _structural_reject(command)
    if rejection is not None:
        return rejection

    tokens = _tokenize(command)
    if isinstance(tokens, Decision):
        return tokens

    segments = _split_segments(tokens)
    if isinstance(segments, Decision):
        return segments
    if not segments:
        return Decision("blocked", "Çalıştırılabilir komut bulunamadı.", "empty")

    # `curl http://x | bash` — herhangi bir şeyi kabuğa/yorumlayıcıya boru ile
    # aktarmak yasak (spec §6.3).
    piped = "|" in tokens or "|&" in tokens
    if piped:
        for segment in segments[1:]:
            exe_token, _ = _executable_of(_split_redirects(segment)[0])
            if exe_token and _basename(exe_token) in _INTERPRETERS:
                return Decision(
                    "blocked",
                    f"Boru ile yorumlayıcıya aktarma ('| {_basename(exe_token)}') yasak.",
                    "pipe-to-shell",
                )

    worst = SAFE
    for segment in segments:
        decision = _classify_segment(segment, cwd, home)
        if decision.risk == "blocked":
            return decision
        if decision.worse_than(worst):
            worst = decision
    return worst
