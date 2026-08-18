"""Masaüstü uygulaması (Faz 8).

Kullanıcının isteği: "servisleri uygulama açılınca açılıp uygulama kapanınca
kapanmasını istiyorum". Bu paket tam olarak onu yapar:

- `supervisor.py` — LiteLLM (:4000) ve FastAPI (:8080) süreçlerini başlatır,
  hazır olmalarını bekler, çıkışta ikisini de öldürür.
- `launcher.py`   — native bir pencere açar (pywebview/WebKitGTK) ve içine
  UI'ı yükler. Pencere kapanınca süpervizör devreye girer.

`install.sh` ve systemd birimleri (Faz 7) hâlâ duruyor ve DEĞİŞMEDİ — o yol
"servisler arka planda hep açık kalsın" isteyenler için. Bu paket ise
"uygulamayı açınca başlasın, kapatınca dursun" yolu. İkisi çakışmaz:
süpervizör zaten dinlenen bir portu görürse o servisi başlatmaz ve
kapanışta ona dokunmaz (bkz. `ServiceHandle.adopted`).
"""
