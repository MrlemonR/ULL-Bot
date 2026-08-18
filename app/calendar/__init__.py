"""Uygulamanın kendi takvimi (Faz 8).

Google Calendar / CalDAV **bilinçli olarak yok** — kullanıcının kararı
(bkz. DECISIONS.md). Etkinlikler SQLite'ta durur, dışarıyla alışveriş ICS
dosyalarıyla olur:

- **İçe:** maildeki `text/calendar` eki (`ics.parse_ics`) → etkinlik
- **Dışa:** `ics.build_ics` → başka bir takvim uygulamasına aktarım

Hatırlatma OS'un kendi bildirim sistemine gider (`app/notify`), bu makinede
dunst. Telefon bildirimi bu fazın kapsamında değil, sonraya bırakıldı.

Not: paket adı stdlib'in `calendar` modülüyle aynı ama Python 3 mutlak
import kullandığı için çakışma yok — `import calendar` her yerde stdlib'i,
`from app.calendar import ...` bunu getirir.
"""
