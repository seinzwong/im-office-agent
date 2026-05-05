from __future__ import annotations

import json
from pathlib import Path

from message_structuring.config import load_config_from_env


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def main() -> None:
    cfg = load_config_from_env()
    model_dir = Path(cfg.importance_bert_model_path)
    test_path = Path("data/importance/test.jsonl")
    if not model_dir.exists():
        print(f"SKIP: model not found at {model_dir}")
        return
    if not test_path.exists():
        print("SKIP: test dataset missing, run build_importance_dataset.py first")
        return

    try:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
    except Exception as exc:
        raise RuntimeError(f"transformers/torch unavailable: {exc}") from exc

    tokenizer = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(str(model_dir), local_files_only=True)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device)
    model.eval()

    rows = _read_jsonl(test_path)
    errors_abs = []
    errors_sq = []
    examples = []

    with torch.no_grad():
        for row in rows:
            text = str(row.get("text", ""))
            label = float(row.get("label", 0.0))
            inputs = tokenizer(
                text,
                truncation=True,
                padding="max_length",
                max_length=cfg.importance_max_length,
                return_tensors="pt",
            )
            inputs = {k: v.to(device) for k, v in inputs.items()}
            out = model(**inputs)
            logits = out.logits
            if logits.ndim == 2 and logits.shape[-1] == 1:
                pred = float(logits[0][0].item())
            else:
                pred = float(logits.flatten()[0].item())
            if pred < 0.0 or pred > 1.0:
                pred = float(torch.sigmoid(torch.tensor(pred)).item())
            pred = max(0.0, min(pred, 1.0))
            errors_abs.append(abs(pred - label))
            errors_sq.append((pred - label) ** 2)
            if len(examples) < 5:
                examples.append({"text": text[:80], "label": round(label, 4), "pred": round(pred, 4)})

    mae = sum(errors_abs) / len(errors_abs) if errors_abs else 0.0
    mse = sum(errors_sq) / len(errors_sq) if errors_sq else 0.0
    print(f"MAE={mae:.6f}")
    print(f"MSE={mse:.6f}")
    print("Examples:")
    for ex in examples:
        print(json.dumps(ex, ensure_ascii=False))


if __name__ == "__main__":
    main()
