"""Kural tabanlı görev sınıflandırıcı (spec §5.2).

**LLM sınıflandırıcı aşaması bilinçli olarak yok.** Spec §5.2 iki aşama
tanımlıyor: kural tabanlı ön eleme, sonra kural karar veremezse bir modele
tek satırlık sınıflandırma sorusu. İkinci aşama şu an anlamsız çünkü:

- Faz 5'e kadar local model yok, yani sınıflandırma için de bir API
  sağlayıcısı çağrılması gerekirdi.
- Bu, her kullanıcı isteğini iki LLM çağrısına çıkarır (bir sınıflandırma +
  bir gerçek cevap) — Gemini'nin günde 20 istek gibi dar kotaları varken bu
  kabul edilemez bir maliyet.

Kural tabanlı sinyaller (uzunluk, anahtar kelime, ara adım olup olmadığı)
spec'in kendi "kabul kriteri"ni (uzun döküman → `long_context`, "merhaba" →
`trivial`) token harcamadan karşılıyor. Karar veremediği durumda spec'in
"confidence < 0.5 ise reasoning'e düş" kuralı zaten en güvenli varsayılan —
LLM aşaması eklenmeden de bu düşülüyor. Gerekçe: DECISIONS.md → "Sınıflandırıcı
kural tabanlı, LLM değil (Faz 4)".
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

LONG_CONTEXT_CHARS = 20_000
TRIVIAL_MAX_CHARS = 80

_WORD_RE = re.compile(r"[a-zçğıöşü]+", re.IGNORECASE)

_CODE_KEYWORDS = (
    "kod", "fonksiyon", "function", "def ", "class ", "import ", "script",
    "bug", "hata ayıkla", "exception", "traceback", "stack trace", "compile",
    "derle", "syntax", "refactor", "unit test", "pytest", "regex", " sql",
    "api endpoint", "json", "yaml", "dockerfile", "```",
)

_REASONING_KEYWORDS = (
    "planla", "karşılaştır", "analiz", "neden", "niçin", "strateji",
    "tasarla", "değerlendir", "adım adım", "artı eksi", "trade-off",
    "mimari", "review", "gözden geçir",
)

_GREETING_WORDS = (
    "merhaba", "selam", "naber", "günaydın", "iyi akşamlar", "iyi geceler",
    "nasılsın", "teşekkür", "sağol", "sağ ol", "görüşürüz", "hoşça kal",
)

# Türkçe fiil çekim ekleri: kelime SONU eşleşmesi aranıyor (substring değil).
# Tam bir morfoloji analizcisi değil — spec §5.2'nin istediği kaba "fiil
# içeriyor mu" sinyali için yeterli.
_VERB_SUFFIXES = (
    "yor", "dı", "di", "du", "dü", "tı", "ti", "tu", "tü", "miş", "muş",
    "mış", "müş", "ecek", "acak", "meli", "malı", "mek", "mak",
)

# Kısa emir kipi fiiller ("aç", "et" gibi) SUFFIX olarak aranırsa "kaç",
# "saat" gibi kelimelere yanlışlıkla çarpar — bunlar TAM kelime eşleşmesi
# istiyor.
_IMPERATIVE_VERBS = {
    "ver", "et", "yap", "oku", "yaz", "sil", "bul", "aç", "kapat",
    "çalıştır", "listele", "göster", "başlat", "durdur", "düzelt", "ekle",
    "güncelle", "kaldır", "ara",
}


@dataclass(frozen=True)
class Classification:
    task_type: str
    confidence: float
    reason: str


def _has_verb(text: str) -> bool:
    for word in _WORD_RE.findall(text.lower()):
        if word in _IMPERATIVE_VERBS:
            return True
        if any(word.endswith(suffix) for suffix in _VERB_SUFFIXES):
            return True
    return False


def _matches_any(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in keywords)


def classify(
    message: str,
    *,
    attachment_chars: int = 0,
    has_image: bool = False,
) -> Classification:
    """Kural tabanlı sınıflandırma (spec §5.2 madde 1).

    Sıra en kesin sinyalden en belirsize doğru: uzunluk ve görsel eki
    tartışmasız; kısa+fiilsiz mesaj güçlü bir "trivial" sinyali; anahtar
    kelimeler daha zayıf bir sinyal; hiçbiri tutmazsa güvenli varsayılan.
    """
    total_len = len(message) + attachment_chars
    if total_len > LONG_CONTEXT_CHARS:
        return Classification(
            "long_context", 1.0, f"toplam uzunluk {total_len} > {LONG_CONTEXT_CHARS}"
        )

    if has_image:
        return Classification("vision", 1.0, "görsel ek var")

    stripped = message.strip()
    if len(stripped) < TRIVIAL_MAX_CHARS and (
        _matches_any(stripped, _GREETING_WORDS) or not _has_verb(stripped)
    ):
        return Classification("trivial", 0.9, "kısa ve fiil içermiyor")

    if _matches_any(stripped, _CODE_KEYWORDS):
        return Classification("code", 0.7, "kod anahtar kelimesi bulundu")

    if _matches_any(stripped, _REASONING_KEYWORDS):
        return Classification(
            "reasoning", 0.7, "planlama/analiz anahtar kelimesi bulundu"
        )

    # Kural karar veremedi. Modül docstring'inde açıklandığı gibi LLM
    # aşaması yok — spec §5.2'nin "confidence < 0.5 ise reasoning'e düş"
    # kuralına doğrudan atlanıyor (en güvenli varsayılan).
    return Classification(
        "reasoning", 0.4, "kural karar veremedi, güvenli varsayılana düşüldü"
    )


_cache: dict[tuple[str, str], Classification] = {}


def classify_cached(
    session_id: str,
    message: str,
    *,
    attachment_chars: int = 0,
    has_image: bool = False,
) -> Classification:
    """Aynı konuşmada aynı girdi ikinci kez sınıflandırılmasın (spec §5.2:
    "sınıflandırma sonucu session_id + girdinin hash'i ile cache'lensin").
    """
    digest = hashlib.sha256(message.encode("utf-8")).hexdigest()
    key = (session_id, digest)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    result = classify(message, attachment_chars=attachment_chars, has_image=has_image)
    _cache[key] = result
    return result
