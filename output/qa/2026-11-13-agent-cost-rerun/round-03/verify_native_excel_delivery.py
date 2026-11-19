"""真实成功Run的HTTP原生xlsx逐格核对；不重建工作簿，不派发模型。"""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import posixpath
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import httpx
from sqlalchemy import text
from core.authn.auth import create_access_token
from core.db.database import SessionLocal
from core.db.model_defs import AgentRun, User
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.excel_export import EXPORT_COLUMNS


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def safe_text(value):
    """独立按导出文档约定投影，不能借生产_project_case镜像映射错误。"""
    value = "" if value is None else re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value))
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def text_lines(values):
    return [value for item in (values if isinstance(values, list) else [values])
            if (value := safe_text(item).strip())]


def numbered(values):
    return "\n".join(value if re.match(r"^\s*(?:\d+\s*[.、)]|[（(]\s*\d+\s*[）)])\s*", value)
                     else f"{index}. {value}" for index, value in enumerate(text_lines(values), 1))


def case_row(case):
    values = {key: case.get(key) for key in ("case_id", "title", "module", "test_input", "priority")}
    values.update(preconditions="\n".join(text_lines(case["preconditions"])),
        steps=numbered([step["action"] for step in case["steps"]]),
        expected_results=numbered([step["expected"] for step in case["steps"]]))
    return [safe_text(values[field]) for _, field in EXPORT_COLUMNS]


def native_cells(body):
    """直接读取原始ZIP/OpenXML，逐格解码，不导入再导出xlsx。"""
    ns = {"m": MAIN_NS}
    with zipfile.ZipFile(io.BytesIO(body)) as package:
        assert package.testzip() is None, "原生xlsx压缩包CRC校验失败"
        book = ET.fromstring(package.read("xl/workbook.xml"))
        sheets = book.findall("m:sheets/m:sheet", ns)
        assert len(sheets) == 1 and sheets[0].attrib["name"] == "测试用例"
        rid = sheets[0].attrib[f"{{{REL_NS}}}id"]
        relationships = ET.fromstring(package.read("xl/_rels/workbook.xml.rels"))
        target = next(value.attrib["Target"] for value in relationships if value.attrib["Id"] == rid)
        sheet_path = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
        shared = []
        if "xl/sharedStrings.xml" in package.namelist():
            for value in ET.fromstring(package.read("xl/sharedStrings.xml")).findall("m:si", ns):
                shared.append("".join(part.text or "" for part in value.findall(".//m:t", ns)))
        sheet = ET.fromstring(package.read(sheet_path))
        rows = sheet.findall("m:sheetData/m:row", ns)
        cells, formulas = {}, []
        for row in rows:
            for cell in row.findall("m:c", ns):
                identity = cell.attrib["r"]
                if cell.find("m:f", ns) is not None:
                    formulas.append(identity)
                kind = cell.attrib.get("t")
                value = cell.findtext("m:v", default="", namespaces=ns)
                if kind == "s":
                    value = shared[int(value)]
                elif kind == "inlineStr":
                    value = "".join(part.text or "" for part in cell.findall("m:is//m:t", ns))
                cells[identity] = value
        return cells, formulas, len(rows), sheet.find("m:dimension", ns).attrib["ref"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    parser.add_argument("--expected-case-count", type=int)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    workbook = output / f"generated-test-cases-run{args.run_id}.xlsx"
    http_report = output / "excel-export-http.json"
    verification = output / "excel-openxml-verification.json"
    assert not any(path.exists() for path in (workbook, http_report, verification)), "禁止覆盖原始导出验收证据，请使用新的输出目录"
    with SessionLocal(autoflush=False) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        run = db.get(AgentRun, args.run_id)
        assert run is not None and run.status == "success", "真实Run尚未成功，禁止导出验收"
        artifact = run.run_context["artifacts"]["test_generation"]
        cases = artifact["test_cases"]
        assert len(cases) == run.input_payload["case_budget"]
        if args.expected_case_count is not None:
            assert len(cases) == args.expected_case_count
        assert len({case["case_id"] for case in cases}) == len(cases)
        protected = action_state_hash([run.input_payload, run.run_context, run.output_payload])
        artifact_hash = action_state_hash(artifact)
        token = create_access_token({"sub": db.get(User, run.user_id).username}, expires_delta=timedelta(minutes=10))
        expected = [[header for header, _ in EXPORT_COLUMNS], *(case_row(case) for case in cases)]
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()

    path = f"/api/agents/runs/{args.run_id}/test-cases.xlsx"
    with httpx.Client(timeout=90, trust_env=False) as client:
        response = client.get(args.api_base.rstrip("/") + path, headers={"Authorization": "Bearer " + token})
    output.mkdir(parents=True, exist_ok=True)
    observed_at = datetime.now(timezone.utc).isoformat()
    body = response.content
    digest = hashlib.sha256(body).hexdigest()
    http_report.write_text(json.dumps({"run_id": args.run_id, "observed_at": observed_at,
        "method": "GET", "path": path, "status": response.status_code,
        "content_type": response.headers.get("content-type"), "content_disposition": response.headers.get("content-disposition"),
        "bytes": len(body), "sha256": digest, "output": str(workbook) if response.status_code == 200 else None}, ensure_ascii=False, indent=2), encoding="utf-8")
    if response.status_code != 200:
        (output / "excel-export-failed-response.bin").write_bytes(body)
    assert response.status_code == 200, f"真实原生导出接口返回HTTP {response.status_code}"
    assert response.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert len(body) == int(response.headers["content-length"])
    workbook.write_bytes(body)
    cells, formulas, rows, dimension = native_cells(body)
    assert not formulas and rows == len(expected)
    expected_cells = {f"{chr(65 + column)}{row}": value for row, values in enumerate(expected, 1)
                      for column, value in enumerate(values)}
    differences = [{"cell": key, "expected": value, "actual": cells.get(key, "")}
                   for key, value in expected_cells.items() if cells.get(key, "") != value]
    assert not differences, f"原生导出与持久交付有{len(differences)}格不同: {differences[:2]}"
    assert not set(cells) - set(expected_cells)
    with SessionLocal(autoflush=False) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        run = db.get(AgentRun, args.run_id)
        assert run.status == "success" and protected == action_state_hash([run.input_payload, run.run_context, run.output_payload])
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    assert hashlib.sha256(workbook.read_bytes()).hexdigest() == digest
    report = {"run_id": args.run_id, "observed_at": observed_at, "verification": "independent_native_openxml_zip_and_cells",
        "status": "all_cells_exactly_match_persisted_delivery", "case_rows": len(cases),
        "columns": len(EXPORT_COLUMNS), "sheet": "测试用例", "range": dimension,
        "compared_cells_including_header": len(expected_cells), "unique_case_ids": len(cases),
        "formula_count": len(formulas), "package_crc_valid": True, "original_sha256": digest,
        "artifact_sha256": artifact_hash, "original_file_unchanged": True, "workbook_edits": 0,
        "database_writes": 0, "model_requests_sent": 0, "historical_run_unchanged": True,
        "scope": "平台真实HTTP原生xlsx字节与持久交付逐格一致；未声称Microsoft Excel应用交互或人工业务执行已验收"}
    verification.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
