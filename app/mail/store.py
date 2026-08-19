"""Mail önbelleği (SQLite).

UI ve ajan araçları **hep buradan** okur, hiçbir zaman doğrudan IMAP'ten.
Sebebi hız ve dayanıklılık: liste görünümü ağ beklemez, sunucu erişilemez
olduğunda da geçmiş maillere bakılabilir, ve kategori/özet gibi bizim
ürettiğimiz alanların yaşayacağı bir yer olur (IMAP'te böyle bir alan yok).

Senkron tek yönlüdür: IMAP → önbellek. Kullanıcının yaptığı değişiklikler
(okundu işaretle, taşı) önce IMAP'e yazılır, başarılı olursa önbelleğe
yansıtılır — tersi olursa UI sunucuda olmayan bir durumu gösterirdi.
"""

from __future__ import annotations

import json
from typing import Any

from app.db.connection import get_connection
from app.mail.classify import HIDDEN_FROM_ALL
from app.mail.parser import ParsedMail, now_utc_iso

# Liste sorgularında gövde taşınmaz; sadece detay açılınca okunur.
LIST_COLUMNS = (
    "id, account_id, folder, uid, message_id, from_name, from_addr, subject, "
    "date_ts, snippet, seen, flagged, answered, category, category_source, "
    "category_reason, summary, summary_at, ics_payload IS NOT NULL AND ics_payload != '' AS has_invite, "
    "attachments"
)


def _row(row) -> dict[str, Any]:
    data = dict(row)
    for field in ("to_addrs", "cc_addrs", "attachments"):
        if field in data:
            data[field] = _load_json(data[field], default=[])
    for field in ("seen", "flagged", "answered", "has_invite"):
        if field in data:
            data[field] = bool(data[field])
    return data


def _load_json(raw: str | None, *, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


# --- hesaplar ---------------------------------------------------------------


def add_account(
    *,
    email: str,
    host: str,
    port: int,
    username: str,
    name: str = "",
    use_ssl: bool = True,
    secret_backend: str = "",
    inbox_folder: str = "INBOX",
    auth_type: str = "password",
) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO mail_accounts "
            "(name, email, host, port, username, use_ssl, auth_type, secret_backend, "
            " inbox_folder, enabled, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, datetime('now')) "
            "ON CONFLICT(email) DO UPDATE SET "
            "  name = excluded.name, host = excluded.host, port = excluded.port, "
            "  username = excluded.username, use_ssl = excluded.use_ssl, "
            "  auth_type = excluded.auth_type, "
            "  secret_backend = excluded.secret_backend, inbox_folder = excluded.inbox_folder, "
            "  enabled = 1, last_error = NULL",
            (name or email, email, host, port, username, int(use_ssl), auth_type,
             secret_backend, inbox_folder),
        )
        conn.commit()
        if cursor.lastrowid:
            return int(cursor.lastrowid)
        row = conn.execute("SELECT id FROM mail_accounts WHERE email = ?", (email,)).fetchone()
        return int(row["id"])


def list_accounts() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, email, host, port, username, use_ssl, auth_type, "
            "       secret_backend, inbox_folder, enabled, created_at, last_sync_at, last_error "
            "FROM mail_accounts ORDER BY id"
        ).fetchall()
    accounts = []
    for row in rows:
        account = dict(row)
        account["use_ssl"] = bool(account["use_ssl"])
        account["enabled"] = bool(account["enabled"])
        accounts.append(account)
    return accounts


def get_account(account_id: int) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM mail_accounts WHERE id = ?", (account_id,)).fetchone()
    if row is None:
        return None
    account = dict(row)
    account["use_ssl"] = bool(account["use_ssl"])
    account["enabled"] = bool(account["enabled"])
    return account


def delete_account(account_id: int) -> bool:
    with get_connection() as conn:
        # ON DELETE CASCADE için foreign_keys pragma'sı bağlantı başına açılmalı.
        conn.execute("PRAGMA foreign_keys = ON")
        cursor = conn.execute("DELETE FROM mail_accounts WHERE id = ?", (account_id,))
        conn.execute("DELETE FROM mail_messages WHERE account_id = ?", (account_id,))
        conn.execute("DELETE FROM mail_sync_state WHERE account_id = ?", (account_id,))
        conn.commit()
    return cursor.rowcount > 0


def set_account_status(account_id: int, *, error: str | None = None, synced: bool = False) -> None:
    with get_connection() as conn:
        if synced:
            conn.execute(
                "UPDATE mail_accounts SET last_sync_at = datetime('now'), last_error = ? WHERE id = ?",
                (error, account_id),
            )
        else:
            conn.execute("UPDATE mail_accounts SET last_error = ? WHERE id = ?", (error, account_id))
        conn.commit()


# --- senkron durumu ---------------------------------------------------------


def get_sync_state(account_id: int, folder: str) -> dict[str, Any]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT uid_validity, last_uid, synced_at FROM mail_sync_state "
            "WHERE account_id = ? AND folder = ?",
            (account_id, folder),
        ).fetchone()
    return dict(row) if row else {"uid_validity": None, "last_uid": 0, "synced_at": None}


def set_sync_state(account_id: int, folder: str, *, uid_validity: int, last_uid: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO mail_sync_state (account_id, folder, uid_validity, last_uid, synced_at) "
            "VALUES (?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(account_id, folder) DO UPDATE SET "
            "  uid_validity = excluded.uid_validity, last_uid = excluded.last_uid, "
            "  synced_at = excluded.synced_at",
            (account_id, folder, uid_validity, last_uid),
        )
        conn.commit()


def reset_folder(account_id: int, folder: str) -> None:
    """UIDVALIDITY değişti — bu klasörün önbelleği artık geçersiz."""
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM mail_messages WHERE account_id = ? AND folder = ?", (account_id, folder)
        )
        conn.execute(
            "DELETE FROM mail_sync_state WHERE account_id = ? AND folder = ?", (account_id, folder)
        )
        conn.commit()


# --- mesajlar ---------------------------------------------------------------


def upsert_message(
    account_id: int,
    folder: str,
    uid: int,
    mail: ParsedMail,
    *,
    seen: bool,
    flagged: bool,
    answered: bool,
    category: str | None = None,
    category_source: str | None = None,
    category_reason: str | None = None,
) -> int:
    """Mesajı önbelleğe yaz. Aynı (hesap, klasör, uid) varsa günceller.

    Kategori sadece BOŞSA yazılır: kullanıcı bir maili elle başka kategoriye
    aldıysa, sonraki bir senkron onu kural tabanlı tahminle geri almamalı.
    """
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO mail_messages
              (account_id, folder, uid, message_id, from_name, from_addr, to_addrs, cc_addrs,
               subject, date_ts, snippet, body_text, body_html, attachments, ics_payload,
               seen, flagged, answered, category, category_source, category_reason, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(account_id, folder, uid) DO UPDATE SET
              seen = excluded.seen,
              flagged = excluded.flagged,
              answered = excluded.answered,
              synced_at = excluded.synced_at,
              category = COALESCE(mail_messages.category, excluded.category),
              category_source = COALESCE(mail_messages.category_source, excluded.category_source),
              category_reason = COALESCE(mail_messages.category_reason, excluded.category_reason)
            """,
            (
                account_id, folder, uid, mail.message_id, mail.from_name, mail.from_addr,
                json.dumps(mail.to_addrs, ensure_ascii=False),
                json.dumps(mail.cc_addrs, ensure_ascii=False),
                mail.subject, mail.date_ts or now_utc_iso(), mail.snippet,
                mail.body_text, mail.body_html,
                json.dumps(mail.attachments, ensure_ascii=False),
                mail.ics_payload,
                int(seen), int(flagged), int(answered),
                category, category_source, category_reason,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM mail_messages WHERE account_id = ? AND folder = ? AND uid = ?",
            (account_id, folder, uid),
        ).fetchone()
    return int(row["id"])


def list_messages(
    *,
    account_id: int | None = None,
    folder: str | None = None,
    category: str | None = None,
    unread_only: bool = False,
    flagged_only: bool = False,
    query: str = "",
    limit: int = 100,
    offset: int = 0,
    include_hidden: bool = False,
    exclude_categories: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Liste görünümünün tek sorgusu. Gövde taşımaz (bkz. `LIST_COLUMNS`).

    Spam (`HIDDEN_FROM_ALL`) hiçbir genel görünüme karışmaz — ne "Tümü"ye,
    ne "Okunmamış"a, ne aramaya. Yalnızca kategorisi doğrudan seçilince
    ya da `include_hidden=True` denince görünür.
    """
    clauses: list[str] = []
    params: list[Any] = []
    if account_id is not None:
        clauses.append("account_id = ?")
        params.append(account_id)
    if folder:
        clauses.append("folder = ?")
        params.append(folder)
    if category:
        clauses.append("category = ?")
        params.append(category)
    elif not include_hidden and HIDDEN_FROM_ALL:
        placeholders = ", ".join("?" for _ in HIDDEN_FROM_ALL)
        clauses.append(f"(category IS NULL OR category NOT IN ({placeholders}))")
        params.extend(sorted(HIDDEN_FROM_ALL))
    if exclude_categories:
        # "Öncelikli" görünümü: reklam/diğer gibi gürültülü kategoriler dışarıda.
        placeholders = ", ".join("?" for _ in exclude_categories)
        clauses.append(f"(category IS NULL OR category NOT IN ({placeholders}))")
        params.extend(exclude_categories)
    if unread_only:
        clauses.append("seen = 0")
    if flagged_only:
        clauses.append("flagged = 1")
    if query.strip():
        escaped = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        clauses.append(
            "(subject LIKE ? ESCAPE '\\' OR from_addr LIKE ? ESCAPE '\\' "
            " OR from_name LIKE ? ESCAPE '\\' OR body_text LIKE ? ESCAPE '\\')"
        )
        params.extend([pattern] * 4)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([limit, offset])
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT {LIST_COLUMNS} FROM mail_messages {where} "
            f"ORDER BY date_ts DESC, id DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
    return [_row(row) for row in rows]


def get_message(message_id: int) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM mail_messages WHERE id = ?", (message_id,)).fetchone()
    return _row(row) if row else None


def counts(account_id: int | None = None) -> dict[str, Any]:
    """Sol şeritteki rozetler: klasör ve kategori kırılımında okunmamış sayısı.

    `total`/`unread` "Tümü" görünümünü anlatır, o yüzden spam'i SAYMAZ —
    liste 200 gösterip rozet 307 deseydi kullanıcı haklı olarak
    "eksik mail var" derdi. Kategori kırılımı ise spam'i de içerir,
    çünkü kendi satırının sayısını oradan alıyor.
    """
    conditions: list[str] = []
    params: list[Any] = []
    if account_id is not None:
        conditions.append("account_id = ?")
        params.append(account_id)
    scope = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    visible = list(conditions)
    visible_params = list(params)
    if HIDDEN_FROM_ALL:
        placeholders = ", ".join("?" for _ in HIDDEN_FROM_ALL)
        visible.append(f"(category IS NULL OR category NOT IN ({placeholders}))")
        visible_params.extend(sorted(HIDDEN_FROM_ALL))
    visible_where = f"WHERE {' AND '.join(visible)}" if visible else ""

    with get_connection() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM mail_messages {visible_where}", visible_params
        ).fetchone()["n"]
        unread = conn.execute(
            f"SELECT COUNT(*) AS n FROM mail_messages {visible_where} "
            f"{'AND' if visible_where else 'WHERE'} seen = 0",
            visible_params,
        ).fetchone()["n"]
        by_category = conn.execute(
            f"SELECT COALESCE(category, 'diger') AS category, COUNT(*) AS total, "
            f"       SUM(CASE WHEN seen = 0 THEN 1 ELSE 0 END) AS unread "
            f"FROM mail_messages {scope} GROUP BY 1 ORDER BY 2 DESC",
            params,
        ).fetchall()
        by_folder = conn.execute(
            f"SELECT folder, COUNT(*) AS total, "
            f"       SUM(CASE WHEN seen = 0 THEN 1 ELSE 0 END) AS unread "
            f"FROM mail_messages {scope} GROUP BY 1 ORDER BY 2 DESC",
            params,
        ).fetchall()
    return {
        "total": total,
        "unread": unread,
        "categories": [dict(row) for row in by_category],
        "folders": [dict(row) for row in by_folder],
    }


def set_flags(message_id: int, *, seen: bool | None = None, flagged: bool | None = None) -> None:
    updates: list[str] = []
    params: list[Any] = []
    if seen is not None:
        updates.append("seen = ?")
        params.append(int(seen))
    if flagged is not None:
        updates.append("flagged = ?")
        params.append(int(flagged))
    if not updates:
        return
    params.append(message_id)
    with get_connection() as conn:
        conn.execute(f"UPDATE mail_messages SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()


def list_messages_for_reclassify(account_id: int | None = None) -> list[dict[str, Any]]:
    """Kural motorundan yeniden geçirilecek satırlar (bkz. `service.reclassify_cached`).

    Liste görünümünün alan listesi yetmiyor: karar için `body_text` ve
    `ics_payload` da lazım.
    """
    query = (
        "SELECT id, folder, message_id, from_name, from_addr, subject, snippet, "
        "body_text, ics_payload, category, category_source FROM mail_messages"
    )
    params: list[Any] = []
    if account_id is not None:
        query += " WHERE account_id = ?"
        params.append(account_id)
    with get_connection() as conn:
        return [dict(row) for row in conn.execute(query, params)]


def set_category(message_id: int, category: str, *, source: str = "user", reason: str = "") -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE mail_messages SET category = ?, category_source = ?, category_reason = ? WHERE id = ?",
            (category, source, reason, message_id),
        )
        conn.commit()


def set_summary(message_id: int, summary: str, *, model: str = "") -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE mail_messages SET summary = ?, summary_model = ?, summary_at = datetime('now') "
            "WHERE id = ?",
            (summary, model, message_id),
        )
        conn.commit()


def remove_message(message_id: int) -> None:
    """IMAP'te başka klasöre taşınan mesajı önbellekten düşür."""
    with get_connection() as conn:
        conn.execute("DELETE FROM mail_messages WHERE id = ?", (message_id,))
        conn.commit()


def uncategorized(limit: int = 20, account_id: int | None = None) -> list[dict[str, Any]]:
    """LLM'e sorulacak adaylar: kuralın kararsız kaldığı (`diger`) mailler."""
    clauses = ["(category IS NULL OR category = 'diger')"]
    params: list[Any] = []
    if account_id is not None:
        clauses.append("account_id = ?")
        params.append(account_id)
    params.append(limit)
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT id, subject, from_name, from_addr, snippet, date_ts "
            f"FROM mail_messages WHERE {' AND '.join(clauses)} "
            f"ORDER BY date_ts DESC LIMIT ?",
            params,
        ).fetchall()
    return [dict(row) for row in rows]


# --- özet kuralları ---------------------------------------------------------


def list_rules(only_enabled: bool = False) -> list[dict[str, Any]]:
    query = "SELECT id, text, enabled, created_at FROM mail_rules"
    if only_enabled:
        query += " WHERE enabled = 1"
    query += " ORDER BY id"
    with get_connection() as conn:
        return [dict(row) for row in conn.execute(query)]


def add_rule(text: str) -> dict[str, Any]:
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO mail_rules (text, enabled, created_at) VALUES (?, 1, ?)",
            (text.strip(), now_utc_iso()),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, text, enabled, created_at FROM mail_rules WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    return dict(row)


def set_rule_enabled(rule_id: int, enabled: bool) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE mail_rules SET enabled = ? WHERE id = ?", (int(enabled), rule_id))
        conn.commit()


def delete_rule(rule_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM mail_rules WHERE id = ?", (rule_id,))
        conn.commit()
