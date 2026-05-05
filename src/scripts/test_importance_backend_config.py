from __future__ import annotations

import os

from message_structuring.components.importance import SummaryCandidateSelector
from message_structuring.config import MessageStructuringConfig
from message_structuring.config import load_config_from_env
from message_structuring.importance_scorers import ModelUnavailableError


def main() -> None:
    old = dict(os.environ)
    try:
        for key in [
            "IMPORTANCE_BACKEND",
            "IMPORTANCE_BERT_MODEL_PATH",
            "IMPORTANCE_BERT_BASE_MODEL",
            "IMPORTANCE_BERT_DEVICE",
            "IMPORTANCE_HYBRID_BERT_WEIGHT",
            "IMPORTANCE_MAX_LENGTH",
        ]:
            os.environ.pop(key, None)

        cfg = load_config_from_env()
        assert cfg.importance_backend == "rule"
        assert cfg.importance_bert_model_path == "models/importance_bert"
        assert cfg.importance_bert_base_model == "hfl/chinese-macbert-base"
        assert cfg.importance_bert_device == "auto"
        assert abs(cfg.importance_hybrid_bert_weight - 0.6) < 1e-9
        assert cfg.importance_max_length == 128

        os.environ["IMPORTANCE_BACKEND"] = "hybrid"
        os.environ["IMPORTANCE_BERT_DEVICE"] = "cpu"
        os.environ["IMPORTANCE_HYBRID_BERT_WEIGHT"] = "0.75"
        os.environ["IMPORTANCE_MAX_LENGTH"] = "256"
        cfg2 = load_config_from_env()
        assert cfg2.importance_backend == "hybrid"
        assert cfg2.importance_bert_device == "cpu"
        assert abs(cfg2.importance_hybrid_bert_weight - 0.75) < 1e-9
        assert cfg2.importance_max_length == 256

        missing_cfg = MessageStructuringConfig(
            importance_backend="bert",
            importance_bert_model_path="models/non_existing_bert_model",
        )
        try:
            SummaryCandidateSelector(config=missing_cfg)
            raise AssertionError("Expected ModelUnavailableError for missing BERT model path")
        except ModelUnavailableError as exc:
            assert "not found" in str(exc).lower()
        print("Importance backend config test passed.")
    finally:
        os.environ.clear()
        os.environ.update(old)


if __name__ == "__main__":
    main()
