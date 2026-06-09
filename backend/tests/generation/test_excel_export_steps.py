from openpyxl import load_workbook

from modules.test_generation_components.export.excel_export import convert_json_to_excel


def _read_first_steps_cell(rows):
    excel_bytes = convert_json_to_excel(rows)
    workbook = load_workbook(filename=__import__("io").BytesIO(excel_bytes))
    sheet = workbook.active
    headers = [cell.value for cell in sheet[1]]
    steps_col = headers.index("steps") + 1
    return sheet.cell(row=2, column=steps_col).value


def _read_sheet(rows):
    excel_bytes = convert_json_to_excel(rows)
    workbook = load_workbook(filename=__import__("io").BytesIO(excel_bytes))
    sheet = workbook.active
    headers = [cell.value for cell in sheet[1]]
    values = [cell.value for cell in sheet[2]]
    return headers, dict(zip(headers, values))


def test_excel_export_does_not_duplicate_existing_step_numbers():
    value = _read_first_steps_cell(
        [
            {
                "id": "TC-001",
                "steps": ["1. open workbook", "2. find question card"],
            }
        ]
    )

    assert value == "1. open workbook\n2. find question card"
    assert "1. 1." not in value
    assert "2. 2." not in value


def test_excel_export_adds_numbers_for_plain_steps():
    value = _read_first_steps_cell(
        [
            {
                "id": "TC-002",
                "steps": ["open workbook", "check list"],
            }
        ]
    )

    assert value == "1. open workbook\n2. check list"


def test_excel_export_includes_persistable_execution_fields():
    headers, row = _read_sheet(
        [
            {
                "id": "TC-001",
                "description": "create plan",
                "test_module": "schedule",
                "preconditions": ["logged in"],
                "steps": ["open planner"],
                "test_input": "valid plan",
                "expected_result": "plan is saved",
                "priority": "P0",
                "priority_final": "P0",
                "workflow_id": "schedule_flow",
                "source_state": "draft",
                "target_state": "saved",
                "execution_group": "main_smoke",
                "role": "student",
                "session_key": "student_session",
                "depends_on": ["TC-000"],
            }
        ]
    )

    assert headers[:8] == [
        "id",
        "description",
        "test_module",
        "preconditions",
        "steps",
        "test_input",
        "expected_result",
        "priority",
    ]
    assert "priority_final" in headers
    assert "workflow_id" in headers
    assert "execution_group" in headers
    assert row["priority_final"] == "P0"
    assert row["workflow_id"] == "schedule_flow"
    assert row["source_state"] == "draft"
    assert row["target_state"] == "saved"
    assert row["execution_group"] == "main_smoke"
    assert row["role"] == "student"
    assert row["session_key"] == "student_session"
    assert row["depends_on"] == "TC-000"
