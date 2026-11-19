"""比较当前 ORM 声明与已采集的真实数据库列，不导入或启动业务服务。"""

import ast
import json
from pathlib import Path


DIRECTORY = Path(__file__).resolve().parent
ROOT = DIRECTORY.parents[2]
report = json.loads((DIRECTORY / "database-readonly.json").read_text(encoding="utf-8"))
declared = {}
for path in (ROOT / "backend/core/db/model_defs").glob("*.py"):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        table_name, columns = None, []
        for item in node.body:
            if not isinstance(item, ast.Assign):
                continue
            for target in item.targets:
                if not isinstance(target, ast.Name):
                    continue
                if target.id == "__tablename__" and isinstance(item.value, ast.Constant):
                    table_name = item.value.value
                if isinstance(item.value, ast.Call) and isinstance(item.value.func, ast.Name) and item.value.func.id == "Column":
                    columns.append(target.id)
        if table_name:
            declared[table_name] = {"model": str(path.relative_to(ROOT)), "columns": sorted(columns)}
comparisons = []
for name, actual in report.get("tables", {}).items():
    model = declared.get(name)
    real_columns = {column["COLUMN_NAME"] for column in actual["columns"]}
    comparisons.append({
        "table": name, "model": model["model"] if model else None,
        "missing_in_database": sorted(set(model["columns"]) - real_columns) if model else None,
        "extra_in_database": sorted(real_columns - set(model["columns"])) if model else None,
    })
result = {"scope": "column_names_only", "comparisons": comparisons}
(DIRECTORY / "schema-model-comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"compared_tables": len(comparisons), "differences": [row for row in comparisons if row["missing_in_database"] or row["extra_in_database"] or not row["model"]]}, ensure_ascii=False))
