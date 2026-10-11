"""Önerilebilecek Ollama modelleri ve donanıma göre uygunluk hesabı.

Boyutlar Ollama kütüphanesindeki varsayılan (Q4) indirme boyutlarıdır,
yaklaşıktır. Listede olmayan herhangi bir model de ayarlardan adıyla
indirilebilir.
"""

from __future__ import annotations

CATEGORIES = {
    "genel": "Genel sohbet",
    "turkce": "Türkçe",
    "kod": "Kod yazma",
    "mantik": "Akıl yürütme / matematik",
    "hafif": "Hafif & hızlı",
    "gorsel": "Resim anlama",
}

CATALOG: list[dict] = [
    # --- hafif ---
    {"name": "gemma3:1b", "size_gb": 0.8, "params": "1B", "tags": ["hafif"],
     "desc": "Çok küçük ve çok hızlı. Basit sorular, özetler; derin sohbet için zayıf."},
    {"name": "llama3.2:3b", "size_gb": 2.0, "params": "3B", "tags": ["hafif", "genel"],
     "desc": "Meta'nın küçük modeli. Hızlı, İngilizcede iyi; Türkçesi orta."},
    {"name": "qwen2.5:3b", "size_gb": 1.9, "params": "3B", "tags": ["hafif", "genel", "turkce"],
     "desc": "Zayıf donanımda bile akıcı. Türkçeyi idare eder, kısa işler için ideal."},
    {"name": "phi4-mini", "size_gb": 2.5, "params": "3.8B", "tags": ["hafif", "mantik"],
     "desc": "Microsoft'un küçük modeli; boyutuna göre matematik ve mantıkta güçlü."},
    {"name": "qwen3:4b", "size_gb": 2.5, "params": "4B", "tags": ["hafif", "mantik"],
     "desc": "Düşünerek cevap verebilen küçük model. Hafif ama akıllı."},
    # --- genel ---
    {"name": "qwen2.5:7b", "size_gb": 4.7, "params": "7B", "tags": ["genel", "turkce"],
     "desc": "Dengeli genel model; Türkçesi iyi, kişilik talimatlarına iyi uyar. En güvenli başlangıç."},
    {"name": "llama3.1:8b", "size_gb": 4.9, "params": "8B", "tags": ["genel"],
     "desc": "Meta'nın popüler modeli. İngilizcede çok iyi, Türkçede orta."},
    {"name": "qwen3:8b", "size_gb": 5.2, "params": "8B", "tags": ["genel", "turkce", "mantik"],
     "desc": "Yeni nesil Qwen; zor sorularda önce düşünür (düşünce bloğu görünür). Çok dilli."},
    {"name": "gemma3:4b", "size_gb": 3.3, "params": "4B", "tags": ["genel", "turkce", "gorsel", "hafif"],
     "desc": "Google'ın çok dilli modeli; Türkçesi doğal. Resimleri de anlayabilir."},
    {"name": "gemma3:12b", "size_gb": 8.1, "params": "12B", "tags": ["genel", "turkce", "gorsel"],
     "desc": "Türkçede en doğal yazanlardan. Yaratıcı yazı ve sohbet için çok iyi; resim anlar."},
    {"name": "mistral-nemo", "size_gb": 7.1, "params": "12B", "tags": ["genel", "turkce"],
     "desc": "Mistral + NVIDIA; çok dilli, uzun bağlamda iyi. Rol yapma/kişilikte başarılı."},
    {"name": "qwen2.5:14b", "size_gb": 9.0, "params": "14B", "tags": ["genel", "turkce"],
     "desc": "7B'nin büyük kardeşi: daha tutarlı, daha bilgili. 12 GB+ VRAM'de rahat."},
    {"name": "qwen3:14b", "size_gb": 9.3, "params": "14B", "tags": ["genel", "turkce", "mantik"],
     "desc": "Düşünen orta boy model; akıl yürütmede güçlü, Türkçesi iyi."},
    {"name": "gemma3:27b", "size_gb": 17.0, "params": "27B", "tags": ["genel", "turkce", "gorsel"],
     "desc": "Üst seviye çok dilli model. 24 GB VRAM ister; Türkçesi çok iyi."},
    {"name": "qwen3:30b", "size_gb": 19.0, "params": "30B (MoE)", "tags": ["genel", "mantik"],
     "desc": "Uzman karışımı (MoE): boyutuna göre hızlı. Güçlü genel model + düşünme."},
    {"name": "qwen2.5:32b", "size_gb": 20.0, "params": "32B", "tags": ["genel", "turkce"],
     "desc": "Çok bilgili ve tutarlı. 24 GB VRAM'de çalışır."},
    # --- kod ---
    {"name": "qwen2.5-coder:1.5b", "size_gb": 1.0, "params": "1.5B", "tags": ["kod", "hafif"],
     "desc": "Küçük kod modeli; kod tamamlama ve basit betikler için."},
    {"name": "qwen2.5-coder:7b", "size_gb": 4.7, "params": "7B", "tags": ["kod"],
     "desc": "Kod yazma, hata ayıklama, açıklama. 8 GB VRAM için en iyi kodcu."},
    {"name": "qwen2.5-coder:14b", "size_gb": 9.0, "params": "14B", "tags": ["kod"],
     "desc": "Daha büyük projelerde daha isabetli kod; 12 GB+ VRAM."},
    {"name": "qwen3-coder:30b", "size_gb": 19.0, "params": "30B (MoE)", "tags": ["kod"],
     "desc": "En güçlü yerel kod modellerinden; MoE olduğu için hızlı. 24 GB VRAM."},
    # --- akıl yürütme ---
    {"name": "deepseek-r1:8b", "size_gb": 5.2, "params": "8B", "tags": ["mantik"],
     "desc": "Uzun uzun düşünüp sonra cevaplar; matematik ve bulmacada güçlü, sohbette yavaş."},
    {"name": "phi4", "size_gb": 9.1, "params": "14B", "tags": ["mantik"],
     "desc": "Matematik, mantık ve teknik sorularda güçlü; İngilizce ağırlıklı."},
    {"name": "deepseek-r1:14b", "size_gb": 9.0, "params": "14B", "tags": ["mantik"],
     "desc": "R1'in orta boyu: daha derin akıl yürütme, 12 GB+ VRAM."},
    {"name": "gpt-oss:20b", "size_gb": 14.0, "params": "20B (MoE)", "tags": ["mantik", "genel"],
     "desc": "OpenAI'ın açık modeli. Akıl yürütme seviyesi yüksek; 16 GB VRAM."},
    # --- görsel ---
    {"name": "llama3.2-vision", "size_gb": 7.8, "params": "11B", "tags": ["gorsel"],
     "desc": "Resimleri anlatır, okur. (Arayüzde resim gönderme henüz yok.)"},
]

BY_NAME = {m["name"]: m for m in CATALOG}

# GPU yokken önerilecek en büyük model (≈7-8B, Q4).
CPU_MAX_GB = 5.5

FIT_LABELS = {
    "gpu": "GPU'da hızlı çalışır",
    "partial": "Kısmen GPU — orta hız",
    "cpu": "CPU/RAM ile çalışır — yavaş",
    "no": "Bellek yetmez",
}


def lookup(name: str) -> dict | None:
    """Kurulu bir modelin katalog kaydı (`qwen2.5:7b` ↔ `qwen2.5:7b-instruct...` gibi)."""
    if name in BY_NAME:
        return BY_NAME[name]
    if name.endswith(":latest") and name[: -len(":latest")] in BY_NAME:
        return BY_NAME[name[: -len(":latest")]]
    return None


def assess(size_gb: float, hw: dict) -> str:
    # Ağırlıklar + bağlam önbelleği (8k bağlam için ~1 GB pay).
    need = size_gb * 1.2 + 1.0
    vram, ram = hw.get("vram_gb") or 0, hw.get("ram_gb") or 0
    if vram >= need:
        return "gpu"
    if hw.get("unified"):
        return "no"
    if vram >= 2 and vram + ram * 0.5 >= need:
        return "partial"
    if ram * 0.7 >= need:
        return "cpu"
    return "no"


def recommend(hw: dict, installed: set[str]) -> list[dict]:
    """Her modeli donanıma göre değerlendir; her kategori için en iyisini işaretle."""
    out = []
    for m in CATALOG:
        fit = assess(m["size_gb"], hw)
        out.append({
            **m,
            "fit": fit,
            "fit_label": FIT_LABELS[fit],
            "installed": m["name"] in installed or f"{m['name']}:latest" in installed,
            "best_for": [],
        })
    for cat in CATEGORIES:
        for level in ("gpu", "partial", "cpu"):
            fits = [m for m in out if cat in m["tags"] and m["fit"] == level]
            if level == "cpu":
                # CPU'da her GB yavaşlık demek: sığsa bile 7-8B'den büyüğünü önerme.
                fits = [m for m in fits if m["size_gb"] <= CPU_MAX_GB] or fits[:1]
            if fits:
                max(fits, key=lambda m: m["size_gb"])["best_for"].append(cat)
                break
    return out
