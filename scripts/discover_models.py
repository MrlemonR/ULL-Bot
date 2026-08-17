"""Kurulum yardımcısı: hangi ücretsiz modeller kullanılabilir? (spec §5.4, §5.5, §12)

İki bağımsız rapor üretir, ikisi de sadece **yazdırır** — hiçbir config
dosyasını kendiliğinden değiştirmez, hiçbir modeli kendiliğinden `ollama
pull` etmez. Gerekçe: spec §12 "model isimlerini uydurma" kuralı, otomatik
config yazımını da tehlikeli kılıyor — kullanıcı önce canlı sonucu görüp
kendi `config/*.yaml`'ını elle güncellemeli (bkz. DECISIONS.md "Ollama
entegrasyonu").

1. `--openrouter`: OpenRouter'ın public model listesini çeker (API anahtarı
   gerekmiyor), `pricing.prompt == "0"` olanları listeler (spec §5.4).
2. `--local`: `nvidia-smi` ile VRAM okur, RTX 5060/8GB gibi kartlar için
   önerilen model sınıflarını (3B-4B sınıflandırıcı/trivial, 7B-8B genel iş)
   ve zaten `ollama list`'te kurulu olanları gösterir (spec §5.5).

Argüman verilmezse ikisi de çalışır.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys

import httpx

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
REQUEST_TIMEOUT_SECONDS = 15

# Öneri listesi — spec §5.5'in "3B-4B sınıflandırıcı/trivial, 7B-8B genel iş"
# tavsiyesinin somut örnekleri. Faz 5'te `qwen2.5:3b-instruct` (2.2 GiB) ve
# `qwen2.5:7b-instruct` (4.7 GiB) canlı denendi (2026-08-16): ikisi de
# `ollama_chat/` üzerinden tool-calling'i destekliyor, RTX 5060/8GB'da GPU'da
# çalışıyor (Vulkan backend). `config/litellm.desktop.yaml`'a girildi.
# Bu liste sadece ÖNERİ — kod hiçbir yerde buna bağımlı değil (spec §12),
# gerçek seçim litellm config'inde yaşıyor.
LOCAL_CANDIDATES = [
    ("sınıflandırıcı/trivial (3B-4B)", "qwen2.5:3b-instruct", 2.2, 3.0),
    ("genel iş (7B-8B)", "qwen2.5:7b-instruct", 4.7, 6.0),
]


def discover_openrouter_free_models() -> int:
    print("== OpenRouter ücretsiz modeller ==")
    try:
        response = httpx.get(OPENROUTER_MODELS_URL, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"OpenRouter'a ulaşılamadı: {exc}")
        return 1

    data = response.json().get("data", [])
    free = [m for m in data if (m.get("pricing") or {}).get("prompt") == "0"]
    if not free:
        print("Ücretsiz model bulunamadı (endpoint boş döndü ya da format değişti).")
        return 1

    for model in sorted(free, key=lambda m: m.get("id", "")):
        context = model.get("context_length", "?")
        print(f"  {model['id']}  (context: {context})")
    print(f"Toplam {len(free)} ücretsiz model. `config/litellm.desktop.yaml`'a "
          "eklemeden önce canlı bir istekle dene (spec §12) — listede olmak "
          "çağrılabilir olmak demek değil.")
    return 0


def _read_vram_mib() -> int | None:
    if shutil.which("nvidia-smi") is None:
        return None
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return None
    line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    try:
        return int(line)
    except ValueError:
        return None


def _installed_ollama_models() -> set[str]:
    if shutil.which("ollama") is None:
        return set()
    try:
        result = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, timeout=10, check=True
        )
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return set()
    names: set[str] = set()
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if parts:
            names.add(parts[0])
    return names


def discover_local_models() -> int:
    print("== Yerel model önerisi (Ollama, spec §5.5) ==")
    if shutil.which("ollama") is None:
        print("Ollama kurulu değil. Arch'ta: `sudo pacman -S ollama` "
              "(+ GPU backend'i: `ollama-vulkan` ya da `ollama-cuda`, "
              "bkz. DECISIONS.md \"Ollama GPU backend'i\").")
        return 1

    vram_mib = _read_vram_mib()
    if vram_mib is None:
        print("`nvidia-smi` bulunamadı ya da çalışmadı — VRAM okunamadı. "
              "Öneri varsayım üzerinden verilmiyor (spec §12); model seçimini "
              "elle yap.")
        return 1

    vram_gib = vram_mib / 1024
    print(f"Algılanan VRAM: {vram_gib:.1f} GiB")
    installed = _installed_ollama_models()

    for label, tag, needs_gib, recommend_min_gib in LOCAL_CANDIDATES:
        status = "kurulu" if tag in installed else "kurulu değil (ollama pull " + tag + ")"
        fits = "sığar" if vram_gib >= needs_gib else "SIĞMAZ"
        print(f"  {label}: {tag}  (~{needs_gib:.1f} GiB, {fits}, {status})")
        if vram_gib < recommend_min_gib:
            print(f"    uyarı: önerilen minimum {recommend_min_gib:.1f} GiB — sınırda, "
                  "başka VRAM tüketen bir şey açıkken sorun çıkabilir")

    print()
    print("Aynı anda iki model yüklemeyin (spec §5.5) — Ollama zaten "
          "`keep_alive` süresi dolunca modeli VRAM'den atıyor "
          "(`config/litellm.desktop.yaml` → `chat-local`: 5 dk).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--openrouter", action="store_true", help="sadece OpenRouter raporu")
    parser.add_argument("--local", action="store_true", help="sadece yerel model raporu")
    args = parser.parse_args(argv)

    run_openrouter = args.openrouter or not (args.openrouter or args.local)
    run_local = args.local or not (args.openrouter or args.local)

    exit_code = 0
    if run_openrouter:
        exit_code |= discover_openrouter_free_models()
    if run_openrouter and run_local:
        print()
    if run_local:
        exit_code |= discover_local_models()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
