from __future__ import annotations

import json
import inspect
from dataclasses import dataclass
from pathlib import Path

from message_structuring.config import load_config_from_env


@dataclass
class _Sample:
    text: str
    label: float


def _read_jsonl(path: Path) -> list[_Sample]:
    rows: list[_Sample] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        data = json.loads(line)
        rows.append(_Sample(text=str(data.get("text", "")), label=float(data.get("label", 0.0))))
    return rows


def main() -> None:
    cfg = load_config_from_env()
    data_dir = Path("data/importance")
    train_path = data_dir / "train.jsonl"
    dev_path = data_dir / "dev.jsonl"
    if not train_path.exists() or not dev_path.exists():
        raise RuntimeError("dataset missing: run build_importance_dataset.py first")

    try:
        import torch
        from datasets import Dataset
        from transformers import (
            AutoModelForSequenceClassification,
            AutoTokenizer,
            Trainer,
            TrainingArguments,
        )
    except Exception as exc:
        raise RuntimeError(f"training dependencies unavailable: {exc}") from exc

    model_source = Path("models/importance_base")
    if not model_source.exists():
        model_source = Path(cfg.importance_bert_base_model)

    tokenizer = AutoTokenizer.from_pretrained(str(model_source))
    model = AutoModelForSequenceClassification.from_pretrained(str(model_source), num_labels=1)

    train_rows = _read_jsonl(train_path)
    dev_rows = _read_jsonl(dev_path)
    train_dataset = Dataset.from_dict({"text": [x.text for x in train_rows], "labels": [x.label for x in train_rows]})
    dev_dataset = Dataset.from_dict({"text": [x.text for x in dev_rows], "labels": [x.label for x in dev_rows]})

    def _tok(batch):
        return tokenizer(batch["text"], truncation=True, padding="max_length", max_length=cfg.importance_max_length)

    train_dataset = train_dataset.map(_tok, batched=True)
    dev_dataset = dev_dataset.map(_tok, batched=True)
    train_dataset.set_format(type="torch", columns=["input_ids", "attention_mask", "labels"])
    dev_dataset.set_format(type="torch", columns=["input_ids", "attention_mask", "labels"])

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device)
    out_dir = Path(cfg.importance_bert_model_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    ta_kwargs = {
        "output_dir": str(out_dir / "checkpoints"),
        "num_train_epochs": 2,
        "per_device_train_batch_size": 8,
        "per_device_eval_batch_size": 8,
        "save_strategy": "epoch",
        "logging_steps": 20,
        "learning_rate": 2e-5,
        "weight_decay": 0.01,
        "report_to": [],
        "load_best_model_at_end": False,
    }
    ta_sig = inspect.signature(TrainingArguments.__init__)
    if "eval_strategy" in ta_sig.parameters:
        ta_kwargs["eval_strategy"] = "epoch"
    else:
        ta_kwargs["evaluation_strategy"] = "epoch"

    args = TrainingArguments(**ta_kwargs)

    trainer_kwargs = {
        "model": model,
        "args": args,
        "train_dataset": train_dataset,
        "eval_dataset": dev_dataset,
    }
    trainer_sig = inspect.signature(Trainer.__init__)
    if "tokenizer" in trainer_sig.parameters:
        trainer_kwargs["tokenizer"] = tokenizer
    elif "processing_class" in trainer_sig.parameters:
        trainer_kwargs["processing_class"] = tokenizer

    trainer = Trainer(**trainer_kwargs)
    trainer.train()
    model.save_pretrained(out_dir)
    tokenizer.save_pretrained(out_dir)
    print(f"Trained and saved importance BERT model to {out_dir}")


if __name__ == "__main__":
    main()
