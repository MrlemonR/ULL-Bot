"""Kural tabanlı sınıflandırıcı testleri (spec §9 Faz 4 kabul kriteri:

"Uzun döküman yapıştır → `long_context` seçilsin. 'merhaba' → `trivial`."
"""

from __future__ import annotations

from app.router.classifier import LONG_CONTEXT_CHARS, classify, classify_cached


def test_greeting_is_trivial() -> None:
    result = classify("merhaba")
    assert result.task_type == "trivial"


def test_short_question_without_verb_is_trivial() -> None:
    result = classify("saat kaç")
    assert result.task_type == "trivial"


def test_long_input_is_long_context() -> None:
    text = "a" * (LONG_CONTEXT_CHARS + 1)
    result = classify(text)
    assert result.task_type == "long_context"
    assert result.confidence == 1.0


def test_long_attachment_counts_toward_total_length() -> None:
    result = classify("özetle", attachment_chars=LONG_CONTEXT_CHARS + 1)
    assert result.task_type == "long_context"


def test_image_attachment_is_vision_even_if_short() -> None:
    result = classify("bu ne", has_image=True)
    assert result.task_type == "vision"


def test_long_context_wins_over_vision() -> None:
    """Spec §5.2 sırası: uzunluk kontrolü görselden önce."""
    result = classify("a" * (LONG_CONTEXT_CHARS + 1), has_image=True)
    assert result.task_type == "long_context"


def test_code_keyword_is_classified_as_code() -> None:
    # 80 karakterden uzun tutuluyor ki "kısa+fiilsiz" kuralına takılıp
    # trivial'e düşmesin, gerçekten anahtar kelime eşleşmesi test edilsin.
    message = (
        "şu Python fonksiyonunda garip bir bug olduğunu düşünüyorum, "
        "traceback'i inceleyip beraber hata ayıklamak ister misin"
    )
    assert len(message) >= 80
    result = classify(message)
    assert result.task_type == "code"


def test_reasoning_keyword_is_classified_as_reasoning() -> None:
    message = (
        "bu iki mimari yaklaşımı karşılaştırıp artı eksilerini adım adım "
        "değerlendirmeni istiyorum, hangisi daha sürdürülebilir olur"
    )
    assert len(message) >= 80
    result = classify(message)
    assert result.task_type == "reasoning"


def test_unmatched_long_message_falls_back_to_reasoning_with_low_confidence() -> None:
    message = (
        "bu konuda biraz daha detay verir misin, tam anlayamadım ve "
        "kafamda hâlâ netleşmeyen birkaç nokta kaldı, tekrar anlatır mısın"
    )
    assert len(message) >= 80
    result = classify(message)
    assert result.task_type == "reasoning"
    assert result.confidence < 0.5


def test_short_message_with_verb_is_not_trivial() -> None:
    result = classify("dosyayı sil")
    assert result.task_type != "trivial"


def test_classify_cached_reuses_result_for_same_session_and_message(monkeypatch) -> None:
    calls = []
    from app.router import classifier as classifier_module

    original = classifier_module.classify

    def counting_classify(message: str, **kwargs):
        calls.append(message)
        return original(message, **kwargs)

    monkeypatch.setattr(classifier_module, "classify", counting_classify)
    classifier_module._cache.clear()

    first = classify_cached("s1", "merhaba")
    second = classify_cached("s1", "merhaba")
    assert first == second
    assert calls == ["merhaba"], "aynı oturum + aynı mesaj ikinci kez sınıflandırılmamalı"

    classify_cached("s2", "merhaba")
    assert calls == ["merhaba", "merhaba"], "farklı oturum ayrı cache anahtarı"
