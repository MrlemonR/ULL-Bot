"""ULL-Bot eğitim betiği — ayrı Python ortamında (.train-venv) çalışır.

Kullanım:  python train_worker.py config.json

Uygulamanın paketlerini içe aktarmaz; yalnızca torch / transformers / peft
(varsa unsloth) kullanır. İlerlemeyi "@@ULL {json}" satırlarıyla bildirir.
Çıktı: config["out"] klasörüne LoRA adaptörü (adapter_model.safetensors +
adapter_config.json) — Ollama'nın ADAPTER komutu bunu okur.
"""

from __future__ import annotations

import inspect
import json
import math
import os
import sys
import time


def emit(**event) -> None:
    print("@@ULL " + json.dumps(event, ensure_ascii=False), flush=True)


def fail(message: str) -> None:
    emit(stage="hata", error=message)
    sys.exit(1)


FALLBACK_TEMPLATE = (
    "{% for m in messages %}<|{{ m['role'] }}|>\n{{ m['content'] }}{{ eos_token }}\n{% endfor %}"
    "{% if add_generation_prompt %}<|assistant|>\n{% endif %}"
)


def main() -> None:
    cfg = json.load(open(sys.argv[1], encoding="utf-8"))
    emit(stage="kütüphaneler yükleniyor", message="PyTorch yükleniyor…")
    try:
        import torch
    except ImportError:
        fail("PyTorch kurulu değil — İnce ayar sayfasından ORTAMI KUR'a bas.")

    cuda = torch.cuda.is_available()
    vram = torch.cuda.get_device_properties(0).total_memory / 1024**3 if cuda else 0.0
    params_b = float(cfg.get("params_b") or 8)
    # Tam hassasiyet daha iyi uyum sağlar (Ollama adaptörü aynı tabana takıyor)
    # ama 8B için ~20 GB ister; sığmazsa 4-bit QLoRA.
    four_bit = cuda and vram < params_b * 2.2 + 2.5
    emit(message=f"GPU: {torch.cuda.get_device_name(0)} ({vram:.1f} GB)" if cuda else "GPU yok — CPU'da eğitim (çok yavaş)")

    use_unsloth = False
    if cuda and cfg.get("backend", "auto") != "hf":
        try:
            from unsloth import FastLanguageModel  # noqa: F401  (transformers'tan önce içe aktarılmalı)
            use_unsloth = True
        except Exception as e:  # unsloth kurulu değil ya da bu GPU'yu desteklemiyor
            emit(message=f"Unsloth kullanılamıyor ({str(e)[:120]}); PEFT ile devam.")

    from transformers import AutoModelForCausalLM, AutoTokenizer, Trainer, TrainerCallback, TrainingArguments

    # --- temel modeli indir ---
    repo = cfg["hf_4bit"] if (four_bit and use_unsloth) else cfg["hf"]
    local = cfg.get("local_model")
    if not local:
        emit(stage=f"temel model indiriliyor ({repo})", message="İlk seferde birkaç GB; sonra önbellekten gelir.")
        try:
            from huggingface_hub import snapshot_download
            from tqdm.auto import tqdm

            class Progress(tqdm):
                def update(self, n=1):
                    out = super().update(n)
                    if self.total:
                        emit(completed=self.n, of=self.total)
                    return out

            local = snapshot_download(
                repo, tqdm_class=Progress,
                allow_patterns=["*.json", "*.safetensors", "tokenizer*", "*.model", "*.txt", "*.jinja"],
            )
        except Exception as e:
            fail(f"Model indirilemedi ({repo}): {e}")

    # --- model + LoRA ---
    emit(stage="model belleğe yükleniyor", message=("Unsloth" if use_unsloth else "PEFT") + (" · 4-bit QLoRA" if four_bit else ""))
    rank = int(cfg.get("rank", 16))
    max_len = int(cfg.get("max_len", 1024))
    targets = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]
    if use_unsloth:
        from unsloth import FastLanguageModel

        model, tok = FastLanguageModel.from_pretrained(model_name=local, max_seq_length=max_len, load_in_4bit=four_bit, dtype=None)
        model = FastLanguageModel.get_peft_model(
            model, r=rank, lora_alpha=rank, lora_dropout=0, bias="none", target_modules=targets,
            use_gradient_checkpointing="unsloth", random_state=3407,
        )
    else:
        from peft import LoraConfig, get_peft_model

        tok = AutoTokenizer.from_pretrained(local)
        kwargs = {}
        if cuda:
            kwargs["device_map"] = "auto"
            kwargs["torch_dtype"] = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
            if four_bit:
                try:
                    from transformers import BitsAndBytesConfig

                    kwargs["quantization_config"] = BitsAndBytesConfig(
                        load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=kwargs["torch_dtype"],
                    )
                except Exception as e:
                    emit(message=f"bitsandbytes yok ({e}); tam hassasiyet denenecek.")
        model = AutoModelForCausalLM.from_pretrained(local, **kwargs)
        if four_bit and "quantization_config" in kwargs:
            from peft import prepare_model_for_kbit_training

            model = prepare_model_for_kbit_training(model)
        present = {n.split(".")[-1] for n, _ in model.named_modules()}
        model = get_peft_model(model, LoraConfig(
            r=rank, lora_alpha=rank, lora_dropout=0.05, bias="none", task_type="CAUSAL_LM",
            target_modules=[t for t in targets if t in present] or None,
        ))
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    if not getattr(tok, "chat_template", None):
        tok.chat_template = FALLBACK_TEMPLATE

    # --- veri: yalnızca cevap kısmından öğren ---
    emit(stage="veri hazırlanıyor")
    rows = [json.loads(line) for line in open(cfg["data"], encoding="utf-8") if line.strip()]
    features, skipped = [], 0
    for r in rows:
        msgs = r["messages"]
        full = tok.apply_chat_template(msgs, tokenize=False)
        prompt = tok.apply_chat_template(msgs[:-1], tokenize=False, add_generation_prompt=True)
        ids = tok(full, add_special_tokens=False, truncation=True, max_length=max_len)["input_ids"]
        n_prompt = len(tok(prompt, add_special_tokens=False)["input_ids"])
        if n_prompt >= len(ids):
            skipped += 1  # cevap max uzunluğa sığmadı
            continue
        features.append({"input_ids": ids, "labels": [-100] * n_prompt + ids[n_prompt:]})
    if not features:
        fail(f"Hiç örnek eğitilemedi: hepsi {max_len} token sınırını aşıyor. Uzunluk ayarını büyüt.")
    if skipped:
        emit(message=f"{skipped} örnek çok uzun olduğu için atlandı.")

    class DS(torch.utils.data.Dataset):
        def __len__(self):
            return len(features)

        def __getitem__(self, i):
            return features[i]

    def collate(batch):
        n = max(len(b["input_ids"]) for b in batch)
        pad = tok.pad_token_id
        return {
            "input_ids": torch.tensor([b["input_ids"] + [pad] * (n - len(b["input_ids"])) for b in batch]),
            "labels": torch.tensor([b["labels"] + [-100] * (n - len(b["labels"])) for b in batch]),
            "attention_mask": torch.tensor([[1] * len(b["input_ids"]) + [0] * (n - len(b["input_ids"])) for b in batch]),
        }

    batch, accum = 2, 4
    epochs = int(cfg.get("epochs", 3))
    total = max(1, math.ceil(len(features) / (batch * accum))) * epochs

    class Report(TrainerCallback):
        def on_train_begin(self, args, state, control, **kw):
            emit(stage="eğitiliyor", step=0, total=state.max_steps or total)

        def on_step_end(self, args, state, control, **kw):
            emit(step=state.global_step, total=state.max_steps or total)

        def on_log(self, args, state, control, logs=None, **kw):
            if logs and "loss" in logs:
                emit(loss=logs["loss"], step=state.global_step, total=state.max_steps or total)

    bf16 = cuda and torch.cuda.is_bf16_supported()
    wanted = {
        "output_dir": os.path.join(os.path.dirname(cfg["out"]), "checkpoints"),
        "num_train_epochs": epochs,
        "per_device_train_batch_size": batch,
        "gradient_accumulation_steps": accum,
        "learning_rate": float(cfg.get("lr", 2e-4)),
        "lr_scheduler_type": "linear",
        "warmup_steps": max(1, total // 20),
        "logging_steps": 1,
        "save_strategy": "no",
        "report_to": [],
        "bf16": bf16,
        "fp16": cuda and not bf16,
        "optim": "adamw_8bit" if (cuda and four_bit) else "adamw_torch",
        "dataloader_num_workers": 0,
        "remove_unused_columns": False,
        "use_cpu": not cuda,
        "no_cuda": not cuda,
        "seed": 3407,
        "disable_tqdm": True,
    }
    # Sürümler arasında değişen parametreleri ele: yalnızca bu sürümün tanıdıklarını ver.
    known = set(inspect.signature(TrainingArguments.__init__).parameters)
    args = TrainingArguments(**{k: v for k, v in wanted.items() if k in known and not (k == "no_cuda" and "use_cpu" in known)})

    trainer_kwargs = {"model": model, "args": args, "train_dataset": DS(), "data_collator": collate, "callbacks": [Report()]}
    t0 = time.time()
    Trainer(**trainer_kwargs).train()
    emit(stage="adaptör kaydediliyor", message=f"Eğitim {time.time() - t0:.0f} sn sürdü.")
    model.save_pretrained(cfg["out"])
    emit(stage="eğitim bitti", message=f"Adaptör: {cfg['out']}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        import traceback

        traceback.print_exc()
        msg = str(e)
        if "out of memory" in msg.lower():
            msg = "GPU belleği yetmedi — daha küçük bir model seç, uzunluğu düşür ya da LoRA rank'i küçült."
        fail(msg[:400])
