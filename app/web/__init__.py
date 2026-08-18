"""Web araştırma katmanı (Faz 9).

İki yetenek: **arama** (`search.py`) ve **sayfa okuma** (`fetch.py`).
Ajan araçları `app/agent/tools/web.py` üzerinden bunları kullanıyor.

İki kural bu paketin tamamına hâkim:

1. **Web içeriği düşman girdidir** — maille aynı kategoride, hatta daha
   kötüsü: bir sayfa "önceki talimatlarını unut" yazabilir ve o sayfayı
   modelin kendisi seçmiş olur. Bütün çıktılar `untrusted=True` dönüyor.
2. **SSRF savunması zorunlu.** Getirilecek adresi modele bırakıyoruz ve
   model, okuduğu bir sayfadaki metinden etkilenebiliyor. `fetch.py`
   bu yüzden özel/yerel ağlara çıkışı kapatıyor — yoksa `169.254.169.254`
   (bulut metadata) ya da `http://localhost:8080/api/...` (kendi
   API'miz) gibi adresler okunabilirdi.
"""
