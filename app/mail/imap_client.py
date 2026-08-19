"""Çıplak IMAP — `imaplib` üzerine ince bir sarmalayıcı.

**Bu modülün tamamı bloklayıcıdır.** `imaplib` senkron soket kullanır;
buradaki hiçbir fonksiyon doğrudan bir `async def` içinden çağrılmamalı.
Çağıran taraf `app/mail/service.py`, hepsini `asyncio.to_thread` içine alır.

Gmail notu: Gmail IMAP'i uygulama parolası (app password) ister, normal hesap
parolası çalışmaz. Klasör adları da özeldir (`[Gmail]/Çöp Kutusu` gibi,
üstelik hesabın diline göre değişir) — bu yüzden çöp/arşiv klasörünü isimden
tahmin etmiyoruz, LIST cevabındaki RFC 6154 özel kullanım bayraklarından
(`\\Trash`, `\\Archive`) buluyoruz.
"""

from __future__ import annotations

import base64
import imaplib
import re
import socket
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

# Sunucu cevap vermezse kullanıcı arayüzü sonsuza kadar beklemesin.
SOCKET_TIMEOUT = 30
# Tek bir FETCH çağrısında istenecek mesaj sayısı. Çok büyük olursa bazı
# sunucular cevabı kesiyor, çok küçük olursa gidiş-dönüş sayısı artıyor.
FETCH_BATCH = 25

# LIST cevabı:  (\HasNoChildren \Trash) "/" "[Gmail]/&AMcAbwDwICY-"
_LIST_LINE = re.compile(rb'\((?P<flags>[^)]*)\)\s+"(?P<sep>[^"]*)"\s+(?P<name>.*)')
# FETCH cevabındaki UID: `1 (UID 4211 FLAGS (\Seen) BODY[] {2048}`
_UID_IN_RESPONSE = re.compile(rb"UID\s+(\d+)")
_FLAGS_IN_RESPONSE = re.compile(rb"FLAGS\s+\(([^)]*)\)")


class MailError(RuntimeError):
    """IMAP tarafında kullanıcıya gösterilebilir bir hata."""


@dataclass
class Folder:
    name: str
    display: str
    flags: list[str]

    @property
    def is_trash(self) -> bool:
        return "\\Trash" in self.flags

    @property
    def is_archive(self) -> bool:
        return "\\Archive" in self.flags or "\\All" in self.flags

    @property
    def is_junk(self) -> bool:
        return "\\Junk" in self.flags


@dataclass
class RawMessage:
    uid: int
    raw: bytes
    seen: bool
    flagged: bool
    answered: bool


@contextmanager
def connect(
    host: str,
    port: int,
    username: str,
    password: str,
    *,
    use_ssl: bool = True,
) -> Iterator[imaplib.IMAP4]:
    """Bağlan, giriş yap, çıkışta her hâlükârda kapat."""
    try:
        if use_ssl:
            conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(host, port, timeout=SOCKET_TIMEOUT)
        else:
            conn = imaplib.IMAP4(host, port, timeout=SOCKET_TIMEOUT)
    except (OSError, socket.timeout, imaplib.IMAP4.error) as exc:
        raise MailError(f"Sunucuya bağlanılamadı ({host}:{port}): {exc}") from exc

    try:
        conn.login(username, password)
    except imaplib.IMAP4.error as exc:
        detail = _readable(exc)
        try:
            conn.logout()
        except Exception:
            pass
        raise MailError(_login_hint(detail)) from exc

    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass  # klasör seçili değilse close() hata verir, önemsiz
        try:
            conn.logout()
        except Exception:
            pass


def _login_hint(detail: str) -> str:
    """Ham IMAP hatasını kullanıcının yapabileceği bir şeye çevir."""
    upper = detail.upper()
    failed = "AUTHENTICATIONFAILED" in upper or "INVALID CREDENTIALS" in upper

    if failed:
        # Gmail bu hatayı üç farklı sebeple veriyor ve üçünün çözümü farklı.
        # Sıralama, sahada görülme sıklığına göre.
        return (
            f"Giriş reddedildi: {detail}\n\n"
            "Gmail/Google Workspace'te bunun üç yaygın sebebi var:\n"
            "1) KULLANICI ADI tam e-posta adresi değil. Görünen adın "
            "(örn. 'Mrlemon') çalışmaz — 'ad@gmail.com' olmalı.\n"
            "2) Normal hesap parolası girilmiş. Google bunu kabul etmiyor; "
            "16 haneli bir UYGULAMA PAROLASI gerekiyor.\n"
            "3) Uygulama parolası yanlış/iptal edilmiş — yenisini üret.\n\n"
            "Workspace hesabıysa ayrıca yöneticinin IMAP'e izin vermesi gerekir."
        )
    return f"Giriş başarısız: {detail}"


def _readable(exc: Exception) -> str:
    """imaplib istisnaları bazen bytes taşır — okunur hâle getir."""
    args = getattr(exc, "args", ())
    if args and isinstance(args[0], bytes):
        return args[0].decode("utf-8", errors="replace")
    return str(exc)


def _check(status: str, data, action: str):
    if status != "OK":
        detail = b" ".join(item for item in data if isinstance(item, bytes))
        raise MailError(f"{action} başarısız: {detail.decode('utf-8', 'replace') or status}")
    return data


def list_folders(conn: imaplib.IMAP4) -> list[Folder]:
    """Sunucudaki klasörler, RFC 6154 özel kullanım bayraklarıyla birlikte."""
    status, data = conn.list()
    _check(status, data, "Klasör listesi")

    folders: list[Folder] = []
    for line in data:
        if not isinstance(line, bytes):
            continue
        match = _LIST_LINE.match(line)
        if not match:
            continue
        flags = match.group("flags").decode("ascii", "replace").split()
        raw_name = match.group("name").strip()
        if raw_name.startswith(b'"') and raw_name.endswith(b'"'):
            raw_name = raw_name[1:-1]
        name = _decode_folder_name(raw_name)
        if "\\Noselect" in flags:
            continue
        folders.append(Folder(name=name, display=name.split("/")[-1], flags=flags))
    return folders


_MUTF7_CHUNK = re.compile(r"&([A-Za-z0-9+,]*)-")


def _decode_folder_name(raw: bytes) -> str:
    """IMAP'in "modified UTF-7"sini çöz (`&AMcAbwDwICY-` → `Çöp…`).

    Python'da `imap4-utf-7` diye bir codec yok ve `imaplib` kendi
    dönüşümünü dışa açmıyor, o yüzden elle: `&...-` arasındaki gövde,
    `/` yerine `,` kullanan base64'lenmiş UTF-16BE'dir; `&-` ise tek bir
    `&` karakteridir. ASCII klasör adları (çoğunluk) hiç dokunulmadan geçer.
    """
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        # Standart dışı ama sahada görülüyor: sunucu doğrudan UTF-8 gönderiyor.
        return raw.decode("utf-8", errors="replace")

    def _decode_chunk(match: re.Match[str]) -> str:
        chunk = match.group(1)
        if not chunk:
            return "&"
        padded = chunk.replace(",", "/")
        padded += "=" * (-len(padded) % 4)
        try:
            return base64.b64decode(padded).decode("utf-16-be")
        except (ValueError, UnicodeDecodeError):
            return match.group(0)  # çözülemedi, ham hâliyle bırak

    return _MUTF7_CHUNK.sub(_decode_chunk, text)


def select_folder(conn: imaplib.IMAP4, folder: str, *, readonly: bool = True) -> int:
    """Klasörü seç, UIDVALIDITY döndür.

    UIDVALIDITY sunucunun "UID'leri yeniden numaralandırdım" sinyali. Değeri
    değiştiyse önbellekteki UID'ler artık başka mesajları gösteriyor demektir;
    çağıran taraf o klasörün önbelleğini atmalı (`store.reset_folder`).
    """
    status, data = conn.select(_quote(folder), readonly=readonly)
    _check(status, data, f"Klasör seçimi ({folder})")

    status, uidvalidity = conn.response("UIDVALIDITY")
    if status == "OK" and uidvalidity and uidvalidity[0]:
        try:
            return int(uidvalidity[0])
        except (TypeError, ValueError):
            pass
    return 0


def _encode_folder_name(name: str) -> str:
    """Unicode klasör adını IMAP'in "modified UTF-7"sine çevir.

    `_decode_folder_name`in tersi ve onun kadar gerekli: sunucuya GİDEN
    klasör adı da bu kodlamada olmak zorunda (RFC 3501 §5.1.3).

    Eksikliği sahada patladı: Gmail'in Türkçe çöp klasörü `[Gmail]/Çöp
    Kutusu`. Bir maili çöpe taşımak istendiğinde ad ham Unicode olarak
    `imaplib`e gidiyordu, o da ASCII'ye çevirmeye çalışıp
    `UnicodeEncodeError` atıyordu — kullanıcı sadece "Internal Server
    Error" görüyordu. ASCII adlar (çoğunluk) hiç dokunulmadan geçtiği için
    hata yıllarca görünmeyebilirdi.

    Kural: yazdırılabilir ASCII olduğu gibi; `&` → `&-`; geri kalan
    karakter dizileri UTF-16BE → base64 (`/` yerine `,`, padding yok) →
    `&...-`.
    """
    out: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        if not buffer:
            return
        raw = "".join(buffer).encode("utf-16-be")
        encoded = base64.b64encode(raw).decode("ascii").rstrip("=").replace("/", ",")
        out.append(f"&{encoded}-")
        buffer.clear()

    for char in name:
        if char == "&":
            flush()
            out.append("&-")
        elif " " <= char <= "~":
            flush()
            out.append(char)
        else:
            buffer.append(char)
    flush()
    return "".join(out)


def _quote(folder: str) -> str:
    """Klasör adını IMAP'e gönderilebilir hâle getir.

    İki iş: modified UTF-7'ye çevir, sonra tırnakla (boşluklu ve köşeli
    parantezli adlar tırnak ister: `"[Gmail]/Trash"`).
    """
    encoded = _encode_folder_name(folder)
    escaped = encoded.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def search_uids(conn: imaplib.IMAP4, *, since_uid: int = 0, criteria: str = "ALL") -> list[int]:
    """Klasördeki UID'ler, küçükten büyüğe.

    `since_uid` verilirse sadece ondan SONRAKİLER — artımlı senkron böyle
    çalışıyor: en son gördüğümüz UID'den büyükler yeni maillerdir.
    """
    query = f"UID {since_uid + 1}:*" if since_uid else criteria
    status, data = conn.uid("SEARCH", None, query)
    _check(status, data, "Arama")
    if not data or not data[0]:
        return []
    uids = [int(item) for item in data[0].split() if item.isdigit()]
    # `UID n:*` en az bir sonuç döndürmek için son mesajı da verir; onu ele.
    return sorted(uid for uid in uids if uid > since_uid)


def fetch_messages(conn: imaplib.IMAP4, uids: list[int]) -> list[RawMessage]:
    """UID listesini toplu çek. `BODY.PEEK[]` — okundu işaretini DEĞİŞTİRMEZ."""
    messages: list[RawMessage] = []
    for start in range(0, len(uids), FETCH_BATCH):
        batch = uids[start : start + FETCH_BATCH]
        status, data = conn.uid("FETCH", ",".join(str(uid) for uid in batch), "(UID FLAGS BODY.PEEK[])")
        _check(status, data, "Mesaj indirme")
        messages.extend(_parse_fetch(data))
    return messages


def _parse_fetch(data) -> list[RawMessage]:
    """FETCH cevabı düz bir liste değil: (başlık, gövde) tuple'ları + ayraçlar."""
    messages: list[RawMessage] = []
    for item in data:
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        header, body = item[0], item[1]
        if not isinstance(header, bytes) or not isinstance(body, bytes):
            continue
        uid_match = _UID_IN_RESPONSE.search(header)
        if not uid_match:
            continue
        flags_match = _FLAGS_IN_RESPONSE.search(header)
        flags = flags_match.group(1).decode("ascii", "replace") if flags_match else ""
        messages.append(
            RawMessage(
                uid=int(uid_match.group(1)),
                raw=body,
                seen="\\Seen" in flags,
                flagged="\\Flagged" in flags,
                answered="\\Answered" in flags,
            )
        )
    return messages


def fetch_flags(conn: imaplib.IMAP4, uids: list[int]) -> dict[int, dict[str, bool]]:
    """Sadece bayrakları çek — başka bir istemcide okunmuş mailleri yakalamak için."""
    if not uids:
        return {}
    result: dict[int, dict[str, bool]] = {}
    for start in range(0, len(uids), 200):
        batch = uids[start : start + 200]
        status, data = conn.uid("FETCH", ",".join(str(uid) for uid in batch), "(UID FLAGS)")
        _check(status, data, "Bayrak okuma")
        for item in data:
            line = item[0] if isinstance(item, tuple) else item
            if not isinstance(line, bytes):
                continue
            uid_match = _UID_IN_RESPONSE.search(line)
            flags_match = _FLAGS_IN_RESPONSE.search(line)
            if not uid_match:
                continue
            flags = flags_match.group(1).decode("ascii", "replace") if flags_match else ""
            result[int(uid_match.group(1))] = {
                "seen": "\\Seen" in flags,
                "flagged": "\\Flagged" in flags,
                "answered": "\\Answered" in flags,
            }
    return result


def store_flag(conn: imaplib.IMAP4, uid: int, flag: str, *, on: bool) -> None:
    """Bir bayrağı ekle/kaldır (`\\Seen`, `\\Flagged`)."""
    command = "+FLAGS" if on else "-FLAGS"
    status, data = conn.uid("STORE", str(uid), command, f"({flag})")
    _check(status, data, f"Bayrak yazma ({flag})")


def move_message(conn: imaplib.IMAP4, uid: int, destination: str) -> None:
    """Mesajı başka klasöre taşı.

    Önce RFC 6851 `UID MOVE` denenir (atomik, Gmail destekler). Sunucu
    bilmiyorsa klasik yol: COPY → `\\Deleted` → EXPUNGE.
    """
    try:
        status, data = conn.uid("MOVE", str(uid), _quote(destination))
        if status == "OK":
            return
    except imaplib.IMAP4.error:
        pass  # MOVE desteklenmiyor — aşağıdaki yedeğe düş

    status, data = conn.uid("COPY", str(uid), _quote(destination))
    _check(status, data, f"Kopyalama ({destination})")
    store_flag(conn, uid, "\\Deleted", on=True)
    conn.expunge()


def find_special(folders: list[Folder], kind: str) -> str | None:
    """Çöp/arşiv/spam klasörünü bayraklarından bul, adından tahmin etme."""
    for folder in folders:
        if kind == "trash" and folder.is_trash:
            return folder.name
        if kind == "archive" and folder.is_archive:
            return folder.name
        if kind == "junk" and folder.is_junk:
            return folder.name
    return None


def check_connection(
    host: str,
    port: int,
    username: str,
    password: str,
    *,
    use_ssl: bool = True,
) -> list[Folder]:
    """Hesap eklerken "bu bilgiler çalışıyor mu" testi. Hata → `MailError`."""
    with connect(host, port, username, password, use_ssl=use_ssl) as conn:
        return list_folders(conn)
