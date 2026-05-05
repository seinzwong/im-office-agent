# SummaryCandidateSelector

MessageStructuringLayer (MSL) for Feishu chat streams. It normalizes raw events, scores importance, detects deliverable value, groups messages into topics, and updates task-level summary candidates.

## Current Stable Scope (MVP v0.7)
- Importance backend: `rule` (default), `bert` (optional local), `hybrid`.
- Topic backend: `rule` (default), `embedding` (optional local), `hybrid` (rule-first).
- Deliverable detection: rule-based only.
- Summary generation: summary client (`stub` or teammate HTTP).
- Store backend: memory default. Redis integration is deferred for team integration.

## Architecture Overview
- `preprocessor.py`: parse Feishu event into `NormalizedMessage`.
- `components/importance.py`: selects importance scorer backend.
- `components/deliverables.py`: rule-based deliverable signals.
- `components/topic_tracker.py`: selects topic backend.
- `summary_updater.py`: gates summary creation and calls summary client.
- `orchestrator.py`: task lifecycle, event processing pipeline, result assembly.
- `service/api.py`: FastAPI endpoints.

Pipeline:
1. Start task for chat.
2. Receive Feishu event.
3. Normalize + dedup.
4. Run importance / deliverables / topic.
5. If eligible, call summary client.
6. Read task messages/topics/summary/result.

## Quick Start

### 1) Environment
Use Python executable:
- `C:\Anaconda3\envs\summary-selector\python.exe`

Optional:
- copy `.env.example` to `.env` and edit.

### 2) Start API
```powershell
$env:PYTHONPATH = "src"
$env:STORE_BACKEND = "memory"
$env:SUMMARY_CLIENT_MODE = "stub"
$env:IMPORTANCE_BACKEND = "hybrid"
$env:IMPORTANCE_BERT_MODEL_PATH = "models/importance_bert"
$env:TOPIC_BACKEND = "hybrid"
$env:TOPIC_EMBEDDING_MODEL_PATH = "models/topic_embedding"
C:\Anaconda3\envs\summary-selector\python.exe -m uvicorn service.api:app --host 127.0.0.1 --port 8000 --reload
```

### 3) Run Tests
Use:
- `run_all_tests.ps1`

Or run script-by-script (see `run_all_tests.ps1`).

## Runtime Modes

### Importance backend
- `IMPORTANCE_BACKEND=rule`: no model dependency.
- `IMPORTANCE_BACKEND=hybrid`: rule + optional local BERT, auto-fallback to rule.
- `IMPORTANCE_BACKEND=bert`: requires local model path.

### Topic backend
- `TOPIC_BACKEND=rule`: v0.3 policy baseline.
- `TOPIC_BACKEND=hybrid`: rule-first, embedding merge assist only.
- `TOPIC_BACKEND=embedding`: local embedding-only backend.

### Summary client
- `SUMMARY_CLIENT_MODE=stub`: local deterministic behavior for tests.
- `SUMMARY_CLIENT_MODE=http`: calls teammate HTTP summary service.

## Manual API Calls
- Example payloads:
  - `examples/api/start_task.json`
  - `examples/api/feishu_batch_event.json`
- Example script:
  - `examples/api/powershell_api_test.ps1`

## API Endpoints
- `GET /health`
- `POST /tasks/start`
- `POST /tasks/{task_id}/stop`
- `POST /activation/state`
- `POST /events/feishu`
- `POST /events/feishu/batch`
- `GET /tasks/{task_id}`
- `GET /tasks/{task_id}/messages`
- `GET /tasks/{task_id}/topics`
- `GET /tasks/{task_id}/summary`
- `GET /tasks/{task_id}/result`
- `POST /tasks/{task_id}/reprocess/summary`
- `GET /debug/config`

## Notes
- Do not commit local model files under `models/`.
- Do not rely on internet/model download during normal tests.
- Redis backend exists in codebase, but integration is currently deferred.
