# Runtime Modes (MVP v0.8)

## Stable local test mode
Recommended local mode for CI-like testing:
- `STORE_BACKEND=memory`
- `SUMMARY_CLIENT_MODE=stub`
- `IMPORTANCE_BACKEND=hybrid`
- `TOPIC_BACKEND=hybrid`

## Store backend
- `STORE_BACKEND=memory` (default)
- `STORE_BACKEND=redis` exists in code but integration is deferred.

## Summary client
- `SUMMARY_CLIENT_MODE=stub`: deterministic, no external service.
- `SUMMARY_CLIENT_MODE=http`: teammate summary service integration.

Related env:
- `TEAMMATE_SUMMARY_BASE_URL`
- `TEAMMATE_SUMMARY_API_KEY`
- `TEAMMATE_SUMMARY_TIMEOUT_SECONDS`
- `SUMMARY_IMPORTANCE_THRESHOLD`

## Importance backend
- `IMPORTANCE_BACKEND=rule`
- `IMPORTANCE_BACKEND=bert` (requires local model files)
- `IMPORTANCE_BACKEND=hybrid` (rule + optional BERT fallback)

Related env:
- `IMPORTANCE_BERT_MODEL_PATH`
- `IMPORTANCE_BERT_BASE_MODEL`
- `IMPORTANCE_BERT_DEVICE`
- `IMPORTANCE_HYBRID_BERT_WEIGHT`
- `IMPORTANCE_MAX_LENGTH`

## Topic backend
- `TOPIC_BACKEND=rule`
- `TOPIC_BACKEND=embedding` (requires local embedding model)
- `TOPIC_BACKEND=hybrid` (rule-first; embedding only assists merge)

Related env:
- `TOPIC_EMBEDDING_MODEL_PATH`
- `TOPIC_EMBEDDING_BASE_MODEL`
- `TOPIC_EMBEDDING_DEVICE`
- `TOPIC_EMBEDDING_ASSIGN_THRESHOLD`
- `TOPIC_EMBEDDING_UNCERTAIN_THRESHOLD`
- `TOPIC_EMBEDDING_MAX_LENGTH`

## Model file policy
- Do not commit model files under `models/`.
- Download/build models only through explicit scripts.
- Normal test flow should not require internet.
