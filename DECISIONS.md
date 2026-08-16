# Kararlar

Bu dosya, spesifikasyonda belirtilmeyen ama uygulama sırasında verilen kararları
ve gerekçelerini tutar. Amaç: sonradan "neden böyle yapılmış" sorusuna hızlı cevap.

---

## Platformlar arası taşınabilirlik (tüm fazlar)

**Karar:** Geliştirme Linux'te (Arch) yapılıyor, ama Windows'a taşınacağı baştan
biliniyor. Bu yüzden faz 1'den itibaren:

- Dosya yolları her yerde `pathlib.Path` ile; string birleştirme yok.
- Kalıcı veri dizini (`db_path` vb.) `platformdirs` kütüphanesiyle hesaplanıyor.
  Linux'te bu zaten XDG'ye denk düşüyor (`~/.local/share/ai-orchestrator`),
  yani spec §12'deki Arch/XDG gereksinimiyle çakışmıyor; Windows'ta ise
  `%LOCALAPPDATA%\ai-orchestrator` gibi doğru yere düşecek.
- LiteLLM proxy'si geliştirme sırasında Docker'a **bağımlı değil** —
  `litellm[proxy]` paketiyle doğrudan `uv run litellm ...` ile çalıştırılıyor.
  Docker Compose dosyası duruyor (dağıtım/izolasyon için), ama Windows'ta
  Docker Desktop kurulu olmasa bile sistem çalışabilsin diye zorunlu değil.
- Faz 2'ye kadar (safety/sandbox, shell allowlist) POSIX'e özgü hiçbir varsayım
  yok. Faz 2'de shell politikası yazılırken Windows kabuğu (PowerShell/cmd)
  farkı ayrı bir alt karar olarak ele alınacak — şimdiden not: `blocked` liste
  regex'leri POSIX komutlarına göre yazıldı (`sudo`, `rm -rf` vb.), Windows
  eşdeğerleri (`Remove-Item -Recurse -Force`, UAC tetikleyen komutlar) o fazda
  eklenecek.

## Ortam ve paket yönetimi

**Karar:** `uv` ile Python 3.11+ sanal ortamı. Sistem pacman değil, proje
paketleri `uv sync` ile `.venv` içine kurulur — bu Windows'ta da aynı şekilde
çalışır (`uv` Windows'u native destekler).

## Web arayüzü (Faz 1)

**Karar:** Spec htmx'i tercih ediyordu ama Faz 1'de tek bir etkileşim var
(mesaj gönder → stream cevap). htmx'in SSE extension'ını eklemek bu kadar
basit bir akış için gereksiz bağımlılık. Bunun yerine vanilla JS +
`fetch` + `ReadableStream` kullanıldı. Faz 7'de UI büyüyüp (onay diyalogları,
kota paneli, katlanabilir tool blokları) daha fazla etkileşim gerektiğinde
htmx'e geçmek yeniden değerlendirilebilir — spec zaten "Claude Code hangisi
daha temizse seçsin" diyor.

## Varsayılan model (Faz 1)

**Karar:** `openrouter/openai/gpt-oss-20b:free` — 2026-08-16 tarihinde
OpenRouter'ın `GET /api/v1/models` uç noktasından canlı çekilen, `pricing.prompt
== "0"` olan modeller arasından seçildi (uydurulmadı). Bu liste sık değişiyor;
başka bir ücretsiz model tercih edilirse `config/litellm.desktop.yaml`'daki
`model` alanı değiştirilmesi yeterli — orchestrator kodu `chat-default` gibi
soyut bir `model_name` ile konuşuyor, provider/model string'i sadece LiteLLM
config'inde yaşıyor (spec §1'deki "orchestrator sağlayıcılardan habersiz
kalır" ilkesi).

## Veritabanı şeması (Faz 1)

**Karar:** Spec §4.4'teki tüm tablolar (`usage_events`, `provider_state`,
`sessions`, `messages`, `memory_notes`) Faz 1'de oluşturuluyor, ama sadece
`sessions` ve `messages` bu fazda gerçekten kullanılıyor (temel sohbet
geçmişi). `usage_events` ve `provider_state` şeması hazır duruyor; bu
tablolara yazma mantığı Faz 3'ün (`quota/tracker.py`) sorumluluğu — şimdiden
kısmi/tahmini bir sayaç mantığı eklenmedi ki Faz 3 tasarımıyla çakışmasın.
