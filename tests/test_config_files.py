"""Config dosyalarının kendi aralarında tutarlı olduğunu doğrular (Faz 6).

`config/litellm.*.yaml` bu projenin Python testleri tarafından hiç
yüklenmiyor — ayrı bir süreç (`litellm` proxy) okuyor. Ama `routing.yaml`
her profil için hangi `model_name`lere referans verdiğini biliyor; o isim
ilgili `litellm.<profil>.yaml`'da yoksa gerçek çalışma zamanında sessizce
"model bulunamadı" hatası alınır, testte değil. Bu dosya o boşluğu kapatıyor.
"""

from __future__ import annotations

from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _litellm_model_names(profile: str) -> set[str]:
    raw = yaml.safe_load((CONFIG_DIR / f"litellm.{profile}.yaml").read_text(encoding="utf-8"))
    return {entry["model_name"] for entry in raw["model_list"]}


def _routing() -> dict:
    return yaml.safe_load((CONFIG_DIR / "routing.yaml").read_text(encoding="utf-8"))


def test_desktop_and_laptop_litellm_configs_declare_the_same_models() -> None:
    """İki profil dosyası birbirinden kopyalanıp elle bakımı yapılıyor —

    biri güncellenip diğeri unutulursa (spec §12'nin "model adını uydurma"
    kuralının bir türevi: config'ler arasında sessiz sapma da tehlikeli).
    """
    assert _litellm_model_names("desktop") == _litellm_model_names("laptop")


def test_every_routing_model_name_exists_in_its_profiles_litellm_config() -> None:
    routing = _routing()
    for profile, blocks in routing["profiles"].items():
        available = _litellm_model_names(profile)
        for task_type, candidates in blocks.items():
            for candidate in candidates or []:
                assert candidate["model"] in available, (
                    f"{profile}.{task_type}: '{candidate['model']}' "
                    f"litellm.{profile}.yaml'da tanımlı değil"
                )


def test_laptop_routing_never_references_ollama() -> None:
    """spec §5.1: laptop profilinde local tamamen çıkarılır (statik)."""
    routing = _routing()
    for task_type, candidates in routing["profiles"]["laptop"].items():
        providers = {c["provider"] for c in candidates or []}
        assert "ollama" not in providers, f"laptop.{task_type} local'i içeriyor"
        assert "local" not in providers, f"laptop.{task_type} local'i içeriyor"
