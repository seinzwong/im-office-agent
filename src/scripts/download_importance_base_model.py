from __future__ import annotations

from pathlib import Path

from message_structuring.config import load_config_from_env


def main() -> None:
    cfg = load_config_from_env()
    out_dir = Path("models/importance_base")
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
    except Exception as exc:
        raise RuntimeError(f"transformers not available: {exc}") from exc

    model_name = cfg.importance_bert_base_model
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForSequenceClassification.from_pretrained(model_name, num_labels=1)
    tokenizer.save_pretrained(out_dir)
    model.save_pretrained(out_dir)
    print(f"Downloaded base model '{model_name}' to {out_dir}")


if __name__ == "__main__":
    main()
