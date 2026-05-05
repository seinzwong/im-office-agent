$ErrorActionPreference = "Stop"

$env:PYTHONPATH = "src"
$env:STORE_BACKEND = "memory"
$env:SUMMARY_CLIENT_MODE = "stub"
$env:IMPORTANCE_BACKEND = "hybrid"
$env:IMPORTANCE_BERT_MODEL_PATH = "models/importance_bert"
$env:TOPIC_BACKEND = "hybrid"
$env:TOPIC_EMBEDDING_MODEL_PATH = "models/topic_embedding"

$pythonExe = "C:\Anaconda3\envs\summary-selector\python.exe"
$tests = @(
    "src\scripts\test_preprocessor.py",
    "src\scripts\test_e2e.py",
    "src\scripts\test_fixtures.py",
    "src\scripts\test_multi_message_task.py",
    "src\scripts\test_topic_policy.py",
    "src\scripts\test_topic_eval.py",
    "src\scripts\test_summary_client_contract.py",
    "src\scripts\test_summary_llm_integration.py",
    "src\scripts\test_api_contract.py",
    "src\scripts\test_store_contract.py",
    "src\scripts\test_event_buffer_contract.py",
    "src\scripts\test_importance_backend_rule.py",
    "src\scripts\test_importance_backend_hybrid_fallback.py",
    "src\scripts\test_importance_backend_config.py",
    "src\scripts\test_importance_backend_bert_local.py",
    "src\scripts\test_topic_backend_rule.py",
    "src\scripts\test_topic_backend_config.py",
    "src\scripts\test_topic_backend_hybrid_fallback.py",
    "src\scripts\test_topic_backend_embedding_local.py",
    "src\scripts\test_topic_embedding_eval.py"
)

foreach ($testFile in $tests) {
    Write-Host "Running $testFile"
    & $pythonExe $testFile
    if ($LASTEXITCODE -ne 0) {
        throw "Test failed: $testFile"
    }
}

Write-Host "All tests passed in stable local mode."
