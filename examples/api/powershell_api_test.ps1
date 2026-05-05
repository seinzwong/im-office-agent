param(
    [string]$BaseUrl = "http://127.0.0.1:8000"
)

$ErrorActionPreference = "Stop"

Write-Host "Health check"
$health = Invoke-RestMethod -Method Get -Uri "$BaseUrl/health"
$health | ConvertTo-Json -Depth 10

Write-Host "Start task"
$startBody = Get-Content "examples/api/start_task.json" -Raw
$task = Invoke-RestMethod -Method Post -Uri "$BaseUrl/tasks/start" -ContentType "application/json" -Body $startBody
$task | ConvertTo-Json -Depth 10
$taskId = $task.task_id

Write-Host "Send Feishu batch"
$batchBody = Get-Content "examples/api/feishu_batch_event.json" -Raw
$batch = Invoke-RestMethod -Method Post -Uri "$BaseUrl/events/feishu/batch" -ContentType "application/json" -Body $batchBody
$batch | ConvertTo-Json -Depth 10

Write-Host "Fetch task result"
$result = Invoke-RestMethod -Method Get -Uri "$BaseUrl/tasks/$taskId/result"
$result | ConvertTo-Json -Depth 12

Write-Host "Read debug config"
$config = Invoke-RestMethod -Method Get -Uri "$BaseUrl/debug/config"
$config | ConvertTo-Json -Depth 10

Write-Host "Stop task"
$stop = Invoke-RestMethod -Method Post -Uri "$BaseUrl/tasks/$taskId/stop" -ContentType "application/json" -Body '{"reason":"manual_test_done"}'
$stop | ConvertTo-Json -Depth 10
