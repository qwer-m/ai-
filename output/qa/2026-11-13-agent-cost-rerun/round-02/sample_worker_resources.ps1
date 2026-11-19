param([Parameter(Mandatory=$true)][int]$WorkerPid, [int]$DurationSeconds = 1800)
$taskDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$taskDeadline = (Get-Date).AddSeconds($DurationSeconds)
$taskSamples = Join-Path $taskDirectory 'worker-resource-samples.jsonl'
while ((Get-Date) -lt $taskDeadline) {
    $taskProcess = Get-Process -Id $WorkerPid -ErrorAction Stop
    try { $taskCost = Get-Content (Join-Path $taskDirectory 'cost-progress.json') -Raw | ConvertFrom-Json } catch { Start-Sleep -Seconds 1; continue }
    $taskSample = [ordered]@{
        observed_at_utc = (Get-Date).ToUniversalTime().ToString('o')
        worker_pid = $WorkerPid
        worker_started_at_local = $taskProcess.StartTime.ToString('o')
        cpu_total_seconds = $taskProcess.CPU
        cpu_user_seconds = $taskProcess.UserProcessorTime.TotalSeconds
        cpu_kernel_seconds = $taskProcess.PrivilegedProcessorTime.TotalSeconds
        working_set_bytes = $taskProcess.WorkingSet64
        private_bytes = $taskProcess.PrivateMemorySize64
        cost_observed_at = $taskCost.observed_at
        run_status = $taskCost.run.status
        action = $taskCost.nodes[-1].observed_projection.current_action
        usage = $taskCost.run.usage
        pending_requests = $taskCost.nodes[-1].observed_budget.pending_requests
        active_elapsed_seconds = $taskCost.active_elapsed_seconds
        human_wait_seconds = $taskCost.human_wait_seconds
        limit = '进程CPU增量可与SDK在途并发重叠；包含Worker同进程其它任务，不能与SDK墙钟相加或相减。'
    }
    $taskSample | ConvertTo-Json -Depth 10 -Compress | Add-Content -LiteralPath $taskSamples -Encoding utf8
    if ($taskCost.run.status -in @('succeeded','failed','cancelled','waiting_approval')) { break }
    Start-Sleep -Seconds 10
}
