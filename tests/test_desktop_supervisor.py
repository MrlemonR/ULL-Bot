"""Servis yaşam döngüsü testleri (Faz 8).

Kullanıcının isteği tek cümleydi: "servisleri uygulama açılınca açılıp
uygulama kapanınca kapanmasını istiyorum". Bu dosya o cümlenin üç kırılgan
noktasını kilitliyor:

1. Kapanışta **yetim süreç kalmamalı**. `uv run X` bir sarmalayıcı; sadece
   onu öldürmek asıl süreci hayatta bırakır. Bu yüzden süreç grubuna sinyal
   gönderiyoruz — testler `killpg` çağrıldığını doğruluyor.
2. **Zaten çalışan bir servis benimsenir, kapanışta öldürülmez.** Kullanıcı
   Faz 7'nin systemd birimlerini enable etmişse uygulamayı kapatmak onun
   arka plan servislerini de kapatmamalı.
3. **SIGTERM'e karşı direnen süreç SIGKILL alır.** LiteLLM'in kapanışı
   bazen sürüyor; süresiz beklemek pencereyi kilitlerdi.

Gerçek süreç başlatmıyoruz (test paketi hızlı kalmalı) — `subprocess.Popen`
ve `os.killpg` sahteleniyor. Gerçek uçtan uca doğrulama canlı yapıldı,
bkz. DECISIONS.md "Faz 8 kabul testi".
"""

from __future__ import annotations

import signal

import pytest

from app.desktop import supervisor as sup_module
from app.desktop.supervisor import ServiceSpec, Supervisor


class FakeProcess:
    """`Popen` yerine geçen sahte: sinyalleri kaydeder, ne zaman öleceğini bilir."""

    def __init__(self, pid: int = 1000, dies_on_term: bool = True) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.dies_on_term = dies_on_term
        self.signals: list[int] = []

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def send_signal(self, sig) -> None:
        self.signals.append(int(sig))


@pytest.fixture
def specs():
    return [
        ServiceSpec("litellm", "LiteLLM proxy", ["echo", "a"], 14000, "http://x/litellm"),
        ServiceSpec("api", "ULL-Bot API", ["echo", "b"], 18080, "http://x/api"),
    ]


@pytest.fixture
def harness(monkeypatch, tmp_path):
    """Popen/killpg/port kontrolünü sahtele, çağrıları topla."""
    state = {"processes": [], "killed": [], "open_ports": set(), "healthy": set()}

    def fake_popen(command, **kwargs):
        # Süreç grubu izolasyonu olmadan killpg anlamsız — bu şartı test et.
        assert kwargs.get("start_new_session") is True, (
            "çocuk süreç kendi süreç grubunda başlatılmalı, yoksa killpg tüm ağacı öldüremez"
        )
        process = FakeProcess(pid=1000 + len(state["processes"]))
        state["processes"].append(process)
        return process

    def fake_killpg(pgid, sig):
        state["killed"].append((pgid, int(sig)))
        for process in state["processes"]:
            if process.pid == pgid and process.returncode is None:
                if int(sig) == int(signal.SIGKILL) or process.dies_on_term:
                    process.returncode = 0

    monkeypatch.setattr(sup_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(sup_module.os, "killpg", fake_killpg)
    monkeypatch.setattr(sup_module.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(sup_module, "port_is_open", lambda port, *a, **k: port in state["open_ports"])
    monkeypatch.setattr(sup_module, "_http_ok", lambda url, timeout=2.0: url in state["healthy"])
    monkeypatch.setattr(sup_module, "POLL_INTERVAL", 0.01)
    monkeypatch.setattr(sup_module, "GRACE_SECONDS", 0.2)

    from app.settings import settings

    monkeypatch.setattr(settings, "db_path", str(tmp_path / "t.db"))
    return state


def test_servisler_baslatilir_ve_hazir_beklenir(harness, specs):
    harness["healthy"] = {"http://x/litellm", "http://x/api"}
    supervisor = Supervisor(specs)
    assert supervisor.start_all() is True
    assert len(harness["processes"]) == 2
    assert all(not handle.adopted for handle in supervisor.handles)


def test_calisan_servis_benimsenir_yeniden_baslatilmaz(harness, specs):
    """systemd zaten başlatmışsa ikinci bir kopya açılmamalı."""
    harness["open_ports"] = {18080}
    harness["healthy"] = {"http://x/litellm", "http://x/api"}

    supervisor = Supervisor(specs)
    assert supervisor.start_all() is True
    # Sadece litellm başlatıldı; api benimsendi.
    assert len(harness["processes"]) == 1
    handles = {handle.spec.name: handle for handle in supervisor.handles}
    assert handles["api"].adopted is True
    assert handles["litellm"].adopted is False


def test_benimsenen_servis_kapanista_oldurulmez(harness, specs):
    """Açmadığımız bir şeyi kapatmıyoruz — kullanıcının systemd servisi yaşamalı."""
    harness["open_ports"] = {18080}
    harness["healthy"] = {"http://x/litellm", "http://x/api"}
    supervisor = Supervisor(specs)
    supervisor.start_all()
    supervisor.stop_all()

    # Tek bir süreç öldürüldü (litellm), benimsenen api'ye dokunulmadı.
    killed_pids = {pid for pid, _ in harness["killed"]}
    assert killed_pids == {harness["processes"][0].pid}


def test_kapanis_once_sigterm_gonderir(harness, specs):
    harness["healthy"] = {"http://x/litellm", "http://x/api"}
    supervisor = Supervisor(specs)
    supervisor.start_all()
    supervisor.stop_all()

    sent = [sig for _, sig in harness["killed"]]
    assert int(signal.SIGTERM) in sent
    assert int(signal.SIGKILL) not in sent  # nazik sinyal yetti
    assert all(process.returncode is not None for process in harness["processes"])


def test_sigterme_direnen_surec_sigkill_alir(harness, specs):
    harness["healthy"] = {"http://x/litellm", "http://x/api"}
    supervisor = Supervisor(specs)
    supervisor.start_all()
    for process in harness["processes"]:
        process.dies_on_term = False  # inatçı süreç

    supervisor.stop_all()
    sent = [sig for _, sig in harness["killed"]]
    assert int(signal.SIGTERM) in sent
    assert int(signal.SIGKILL) in sent
    assert all(process.returncode is not None for process in harness["processes"])


def test_api_once_durdurulur(harness, specs):
    """API hâlâ LiteLLM'e istek gönderiyor olabilir; ters sırada kapatılır."""
    harness["healthy"] = {"http://x/litellm", "http://x/api"}
    supervisor = Supervisor(specs)
    supervisor.start_all()
    supervisor.stop_all()

    order = [pid for pid, sig in harness["killed"] if sig == int(signal.SIGTERM)]
    api_pid = harness["processes"][1].pid
    litellm_pid = harness["processes"][0].pid
    assert order.index(api_pid) < order.index(litellm_pid)


def test_acilista_olen_surec_hata_olarak_bildirilir(harness, specs, monkeypatch):
    harness["healthy"] = set()  # hiçbiri sağlıklı olmayacak
    supervisor = Supervisor(specs)

    original = sup_module.subprocess.Popen

    def dying_popen(command, **kwargs):
        process = original(command, **kwargs)
        process.returncode = 1  # anında öldü
        return process

    monkeypatch.setattr(sup_module.subprocess, "Popen", dying_popen)
    assert supervisor.start_all() is False
    assert "açılışta durdu" in supervisor.first_error()


def test_hazir_olmayan_servis_zaman_asimina_ugrar(harness, specs, monkeypatch):
    from app.settings import settings

    monkeypatch.setattr(settings, "service_startup_timeout", 0)
    harness["healthy"] = set()
    supervisor = Supervisor(specs)
    assert supervisor.start_all() is False
    assert "hazır olmadı" in supervisor.first_error()


def test_summary_durumu_okunur_bicimde_verir(harness, specs):
    harness["healthy"] = {"http://x/litellm", "http://x/api"}
    supervisor = Supervisor(specs)
    supervisor.start_all()
    rows = supervisor.summary()
    assert [row["name"] for row in rows] == ["litellm", "api"]
    assert all(row["status"] == "çalışıyor" for row in rows)
    assert all(row["port"] for row in rows)


def test_stop_all_iki_kez_cagrilabilir(harness, specs):
    """Pencere `closing` olayı ve `finally` ikisi de çağırıyor — patlamamalı."""
    harness["healthy"] = {"http://x/litellm", "http://x/api"}
    supervisor = Supervisor(specs)
    supervisor.start_all()
    supervisor.stop_all()
    supervisor.stop_all()  # ikinci çağrı sessizce geçmeli


def test_build_specs_profile_gore_config_secer(monkeypatch):
    from app.settings import settings

    monkeypatch.setattr(settings, "profile", "laptop")
    specs = sup_module.build_specs()
    litellm = next(spec for spec in specs if spec.name == "litellm")
    assert "litellm.laptop.yaml" in " ".join(litellm.command)

    monkeypatch.setattr(settings, "profile", "desktop")
    specs = sup_module.build_specs()
    litellm = next(spec for spec in specs if spec.name == "litellm")
    assert "litellm.desktop.yaml" in " ".join(litellm.command)


def test_build_specs_bilinmeyen_profilde_desktopa_duser(monkeypatch):
    from app.settings import settings

    monkeypatch.setattr(settings, "profile", "uydurma")
    litellm = next(spec for spec in sup_module.build_specs() if spec.name == "litellm")
    assert "litellm.desktop.yaml" in " ".join(litellm.command)
