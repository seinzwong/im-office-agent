from __future__ import annotations

from pathlib import Path

from message_structuring.config import load_config_from_env


def main() -> None:
    cfg = load_config_from_env()
    target = Path(cfg.topic_embedding_model_path)
    target.mkdir(parents=True, exist_ok=True)
    model_name = cfg.topic_embedding_base_model

    try:
        from sentence_transformers import SentenceTransformer  # type: ignore

        model = SentenceTransformer(model_name)
        model.save(str(target))
        print(f"Downloaded sentence-transformers model '{model_name}' to {target}")
        return
    except Exception as exc:
        print(
            "sentence-transformers download path unavailable. "
            "Install sentence-transformers explicitly if needed."
        )
        print(f"Reason: {exc}")

    try:
        from transformers import AutoModel, AutoTokenizer

        tok = AutoTokenizer.from_pretrained(model_name)
        mdl = AutoModel.from_pretrained(model_name)
        tok.save_pretrained(target)
        mdl.save_pretrained(target)
        print(f"Downloaded transformers model '{model_name}' to {target}")
    except Exception as exc:
        raise RuntimeError(
            f"Unable to download topic embedding model '{model_name}'. "
            "Check network/dependencies and run again explicitly."
        ) from exc


if __name__ == "__main__":
    main()
