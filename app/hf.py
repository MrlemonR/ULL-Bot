"""Hugging Face'ten model ekleme.

Ollama, Hugging Face'teki GGUF modellerini doğrudan çekebiliyor:
    ollama pull hf.co/<kullanıcı>/<depo>:<kuantizasyon>
Burada kullanıcının yapıştırdığı bağlantıyı çözüyoruz, depodaki GGUF
dosyalarını (kuantizasyon + boyut) listeliyor ve bu bilgisayara en uygun
olanı öneriyoruz. İndirmeyi yine Ollama yapıyor.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from app import catalog

HF_HOST = "https://huggingface.co"

# Kalite sırası: önerirken GPU'ya sığan en iyisi seçilir.
QUALITY = ["Q8_0", "Q6_K", "Q5_K_M", "Q5_K_S", "Q4_K_M", "Q4_K_S", "IQ4_XS", "IQ4_NL", "Q4_0",
           "Q3_K_L", "Q3_K_M", "IQ3_M", "Q3_K_S", "IQ3_XS", "Q2_K", "IQ2_M"]
QUANT_RE = re.compile(r"(?<![A-Za-z0-9])(I?Q\d(?:_[A-Z0-9]+)*|BF16|F16|F32)(?![A-Za-z0-9])", re.I)
SPLIT_RE = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$", re.I)
REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")


def parse_ref(ref: str) -> dict:
    """Bağlantı / 'hf.co/u/r:Q4' / 'u/r' → {repo, quant, file}. Olmazsa ValueError."""
    ref = (ref or "").strip()
    if not ref:
        raise ValueError("Bağlantı boş")
    quant = file = None
    if ref.startswith(("http://", "https://")):
        u = urlparse(ref)
        if u.netloc.lower() not in ("huggingface.co", "www.huggingface.co", "hf.co"):
            raise ValueError("Bu bir Hugging Face bağlantısı değil")
        parts = [p for p in u.path.split("/") if p]
        if len(parts) < 2:
            raise ValueError("Bağlantıda kullanıcı/depo adı yok")
        repo = f"{parts[0]}/{parts[1]}"
        # .../blob/main/model-Q4_K_M.gguf gibi doğrudan dosya bağlantısı
        if len(parts) > 4 and parts[2] in ("blob", "resolve") and parts[-1].lower().endswith(".gguf"):
            file = "/".join(parts[4:])
    else:
        ref = re.sub(r"^(hf\.co|huggingface\.co)/", "", ref)
        repo, _, tag = ref.partition(":")
        quant = tag or None
    if not REPO_RE.match(repo):
        raise ValueError(f"Geçersiz depo adı: {repo}")
    if file and not quant:
        quant = quant_of(file)
    return {"repo": repo, "quant": quant.upper() if quant else None, "file": file}


def quant_of(filename: str) -> str | None:
    """Dosya adındaki SON kuantizasyon etiketi: 'Model-8B-Q4_K_M-imat.gguf' → Q4_K_M."""
    stem = SPLIT_RE.sub("", filename.rsplit("/", 1)[-1])
    stem = re.sub(r"\.gguf$", "", stem, flags=re.I)
    found = QUANT_RE.findall(stem)
    return found[-1].upper() if found else None


def gguf_files(siblings: list[dict]) -> list[dict]:
    """Depodaki GGUF dosyalarını kuantizasyona göre topla (bölünmüş dosyaların boyutları toplanır)."""
    groups: dict[str, dict] = {}
    for s in siblings:
        name = s.get("rfilename", "")
        low = name.lower()
        if not low.endswith(".gguf") or "mmproj" in low.rsplit("/", 1)[-1]:
            continue  # mmproj: görsel modellerin yardımcı dosyası, tek başına model değil
        size = s.get("size") or (s.get("lfs") or {}).get("size") or 0
        quant = quant_of(name) or name.rsplit("/", 1)[-1][:-5]
        g = groups.setdefault(quant, {"quant": quant, "file": name, "size": 0, "parts": 0})
        g["size"] += int(size)
        g["parts"] += 1
        if SPLIT_RE.search(name):
            g["split"] = True
    files = list(groups.values())
    order = {q: i for i, q in enumerate(QUALITY)}
    files.sort(key=lambda f: (order.get(f["quant"], len(QUALITY)), -f["size"]))
    return files


def recommend(files: list[dict], hw: dict) -> str | None:
    """GPU'ya tamamen sığan en kaliteli; hiçbiri sığmıyorsa Q4_K_M ya da en küçüğü."""
    for f in files:
        f["size_gb"] = round(f["size"] / 1024**3, 2) if f["size"] else None
        f["fit"] = catalog.assess(f["size_gb"], hw) if f["size_gb"] else "unknown"
        f["fit_label"] = catalog.FIT_LABELS.get(f["fit"], "boyut bilinmiyor")
    known = [f for f in files if f["quant"] in QUALITY]
    for level in ("gpu", "partial", "cpu"):
        fitting = [f for f in known if f["fit"] == level]
        if fitting:
            if level == "gpu":
                return fitting[0]["quant"]  # dosyalar kalite sırasında
            q4 = next((f for f in fitting if f["quant"] == "Q4_K_M"), None)
            return (q4 or min(fitting, key=lambda f: f["size"]))["quant"]
    if files:
        return min(files, key=lambda f: f["size"] or 1e18)["quant"]
    return None


def suggested_name(repo: str, quant: str | None) -> str:
    name = repo.split("/", 1)[1].lower()
    name = re.sub(r"[-_.]?gguf$", "", name)
    name = re.sub(r"[^a-z0-9._-]+", "-", name).strip("-._")[:48].strip("-._") or "hf-model"
    return f"{name}:{quant.lower()}" if quant else name


async def inspect(client: httpx.AsyncClient, ref: str, hw: dict) -> dict:
    parsed = parse_ref(ref)
    repo = parsed["repo"]
    try:
        r = await client.get(f"/api/models/{repo}", params={"blobs": "true"})
    except httpx.HTTPError as e:
        raise ConnectionError(f"Hugging Face'e ulaşılamadı: {e}") from e
    if r.status_code in (401, 404):
        raise LookupError(f"Depo bulunamadı ya da gizli: {repo}")
    if r.status_code != 200:
        raise ConnectionError(f"Hugging Face hata verdi (HTTP {r.status_code})")
    data = r.json()
    files = gguf_files(data.get("siblings") or [])
    card = data.get("cardData") or {}
    info = {
        "repo": repo,
        "url": f"{HF_HOST}/{repo}",
        "author": data.get("author") or repo.split("/")[0],
        "downloads": data.get("downloads"),
        "likes": data.get("likes"),
        "gated": bool(data.get("gated")),
        "license": card.get("license") if isinstance(card.get("license"), str) else None,
        "base_model": card.get("base_model") if isinstance(card.get("base_model"), str) else None,
        "pipeline": data.get("pipeline_tag"),
        "files": files,
        "requested": parsed["quant"],
        "hardware": {"vram_gb": hw.get("vram_gb"), "ram_gb": hw.get("ram_gb")},
    }
    if not files:
        info["error"] = ("Bu depoda GGUF dosyası yok. Ollama Hugging Face'ten yalnızca GGUF modelleri "
                         "doğrudan çekebiliyor; aynı modelin GGUF sürümünü seç.")
        info["alternatives"] = await alternatives(client, repo)
        return info
    rec = recommend(files, hw)
    chosen = parsed["quant"] if any(f["quant"] == parsed["quant"] for f in files) else rec
    info["recommended"] = rec
    info["selected"] = chosen
    info["suggested_name"] = suggested_name(repo, None)
    return info


async def alternatives(client: httpx.AsyncClient, repo: str) -> list[dict]:
    """Aynı modelin GGUF sürümleri (en çok indirilene göre)."""
    term = re.sub(r"[-_.]?(gguf|hf|instruct)$", "", repo.split("/", 1)[1], flags=re.I)
    try:
        r = await client.get("/api/models", params={"search": term, "filter": "gguf", "sort": "downloads", "limit": 8})
        items = r.json() if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError):
        return []
    return [{"repo": m.get("id") or m.get("modelId"), "downloads": m.get("downloads")} for m in items if m.get("id") or m.get("modelId")]


def ollama_ref(repo: str, quant: str | None) -> str:
    return f"hf.co/{repo}:{quant}" if quant else f"hf.co/{repo}"
