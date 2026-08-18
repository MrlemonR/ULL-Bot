"""IMAP mail katmanı (Faz 8).

Katmanlar, dıştan içe:

- `service.py`  — API'nin ve ajan araçlarının çağırdığı yüzey (senkronla,
  listele, oku, işaretle, taşı). Tek async giriş noktası burası.
- `imap_client.py` — çıplak IMAP protokolü. Bloklayıcı (imaplib), her çağrısı
  `service` tarafından `asyncio.to_thread` içinde çalıştırılır.
- `parser.py`   — RFC822 → düz alanlar. Ağ bilmez, saf fonksiyon, test edilebilir.
- `classify.py` — kural tabanlı kategori. LLM'siz çalışır; LLM sadece
  kararsız kalanları netleştirmek için `service` tarafından çağrılır.
- `store.py`    — SQLite önbelleği. UI hep buradan okur, IMAP'ten değil.
- `secrets.py`  — parola; DB'ye asla yazılmaz.
"""
