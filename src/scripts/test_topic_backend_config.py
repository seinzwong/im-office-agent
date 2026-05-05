from __future__ import annotations

import os

from message_structuring.components.topic_tracker import TopicTracker
from message_structuring.config import MessageStructuringConfig, load_config_from_env
from message_structuring.topic_backends import ModelUnavailableError


def main() -> None:
    old = dict(os.environ)
    try:
        for key in [
            "TOPIC_BACKEND",
            "TOPIC_EMBEDDING_MODEL_PATH",
            "TOPIC_EMBEDDING_BASE_MODEL",
            "TOPIC_EMBEDDING_DEVICE",
            "TOPIC_EMBEDDING_ASSIGN_THRESHOLD",
            "TOPIC_EMBEDDING_UNCERTAIN_THRESHOLD",
            "TOPIC_EMBEDDING_MAX_LENGTH",
        ]:
            os.environ.pop(key, None)

        cfg = load_config_from_env()
        assert cfg.topic_backend == "rule"
        assert cfg.topic_embedding_model_path == "models/topic_embedding"
        assert cfg.topic_embedding_base_model == "BAAI/bge-small-zh-v1.5"
        assert cfg.topic_embedding_device == "auto"
        assert abs(cfg.topic_embedding_assign_threshold - 0.68) < 1e-9
        assert abs(cfg.topic_embedding_uncertain_threshold - 0.58) < 1e-9
        assert cfg.topic_embedding_max_length == 128

        os.environ["TOPIC_BACKEND"] = "hybrid"
        os.environ["TOPIC_EMBEDDING_DEVICE"] = "cpu"
        os.environ["TOPIC_EMBEDDING_ASSIGN_THRESHOLD"] = "0.7"
        os.environ["TOPIC_EMBEDDING_UNCERTAIN_THRESHOLD"] = "0.6"
        os.environ["TOPIC_EMBEDDING_MAX_LENGTH"] = "256"
        cfg2 = load_config_from_env()
        assert cfg2.topic_backend == "hybrid"
        assert cfg2.topic_embedding_device == "cpu"
        assert abs(cfg2.topic_embedding_assign_threshold - 0.7) < 1e-9
        assert abs(cfg2.topic_embedding_uncertain_threshold - 0.6) < 1e-9
        assert cfg2.topic_embedding_max_length == 256

        missing_cfg = MessageStructuringConfig(topic_backend="embedding", topic_embedding_model_path="models/non_existing_topic_embedding")
        try:
            TopicTracker(config=missing_cfg)
            raise AssertionError("Expected ModelUnavailableError for missing topic embedding model")
        except ModelUnavailableError as exc:
            assert "not found" in str(exc).lower()

        print("Topic backend config test passed.")
    finally:
        os.environ.clear()
        os.environ.update(old)


if __name__ == "__main__":
    main()
