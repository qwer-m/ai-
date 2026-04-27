$ErrorActionPreference = 'Stop'

$target = 'backend/modules/test_generation_components/prompting/prompt_orchestration_split_helpers.py'
$backup = 'backend/tmp/_prompt_block_new_backup.py'
$oldOut = 'backend/tmp/prompt_ab_A_old.json'
$newOut = 'backend/tmp/prompt_ab_B_new.json'
$cmpOut = 'backend/tmp/prompt_ab_compare.json'
$reqFile = 'backend/tmp/prompt_ab_requirement.txt'
$projectId = 8
$userId = 1
$runs = 3
$expectedCount = 12

Copy-Item -Path $target -Destination $backup -Force
try {
  $oldContent = git show HEAD:backend/modules/test_generation_components/prompting/prompt_orchestration_split_helpers.py
  Set-Content -Path $target -Value $oldContent -Encoding utf8
  python backend/tmp/prompt_block_single_eval.py --label A_old --runs $runs --project-id $projectId --user-id $userId --requirement-file $reqFile --expected-count $expectedCount --output $oldOut

  Copy-Item -Path $backup -Destination $target -Force
  python backend/tmp/prompt_block_single_eval.py --label B_new --runs $runs --project-id $projectId --user-id $userId --requirement-file $reqFile --expected-count $expectedCount --output $newOut

  @'
import json
from pathlib import Path

A = json.loads(Path("backend/tmp/prompt_ab_A_old.json").read_text(encoding="utf-8"))
B = json.loads(Path("backend/tmp/prompt_ab_B_new.json").read_text(encoding="utf-8"))
sa = A.get("summary", {})
sb = B.get("summary", {})

def f(x):
    try:
        return float(x)
    except Exception:
        return 0.0

delta = {
    "delta_p1": round(f(sb.get("p1_ratio")) - f(sa.get("p1_ratio")), 4),
    "delta_ui": round(f(sb.get("ui_ratio")) - f(sa.get("ui_ratio")), 4),
    "delta_flow": round(f(sb.get("flow_ratio")) - f(sa.get("flow_ratio")), 4),
    "delta_cross_page_count": int((sb.get("structure") or {}).get("cross_page_case_count") or 0) - int((sa.get("structure") or {}).get("cross_page_case_count") or 0),
    "delta_multi_step_count": int((sb.get("structure") or {}).get("multi_step_case_count") or 0) - int((sa.get("structure") or {}).get("multi_step_case_count") or 0),
    "delta_state_transition_count": int((sb.get("structure") or {}).get("state_transition_case_count") or 0) - int((sa.get("structure") or {}).get("state_transition_case_count") or 0),
}

improved = (delta["delta_p1"] > 0) and (delta["delta_ui"] < 0) and (delta["delta_flow"] > 0)

payload = {
    "A": sa,
    "B": sb,
    "delta": delta,
    "conclusion": {
        "improved": "yes" if improved else "no",
        "most_obvious_change": max(
            [
                ("p1_ratio", abs(delta["delta_p1"])),
                ("ui_ratio", abs(delta["delta_ui"])),
                ("flow_ratio", abs(delta["delta_flow"])),
            ],
            key=lambda x: x[1],
        )[0],
        "structural_improvement": bool(
            delta["delta_cross_page_count"] > 0
            or delta["delta_multi_step_count"] > 0
            or delta["delta_state_transition_count"] > 0
        ),
    },
}

Path("backend/tmp/prompt_ab_compare.json").write_text(
    json.dumps(payload, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
print("backend/tmp/prompt_ab_compare.json")
print(json.dumps(payload, ensure_ascii=False))
'@ | python -
}
finally {
  if (Test-Path $backup) {
    Copy-Item -Path $backup -Destination $target -Force
    Remove-Item -Path $backup -Force
  }
}
