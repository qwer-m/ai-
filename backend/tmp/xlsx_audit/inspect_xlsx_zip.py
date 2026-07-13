from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

INPUT = Path(r"C:\Users\Administrator\Downloads\【天天练-活动】期末冲刺营（2期）_测试用例.xlsx")
NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def _read_xml(zf: zipfile.ZipFile, name: str) -> ET.Element:
    return ET.fromstring(zf.read(name))


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = _read_xml(zf, "xl/sharedStrings.xml")
    values: list[str] = []
    for si in root.findall("main:si", NS):
        texts = [node.text or "" for node in si.findall(".//main:t", NS)]
        values.append("".join(texts))
    return values


def _cell_value(cell: ET.Element, shared: list[str]) -> str:
    value = cell.find("main:v", NS)
    if value is None:
        inline = cell.find("main:is", NS)
        if inline is None:
            return ""
        return "".join(node.text or "" for node in inline.findall(".//main:t", NS))
    raw = value.text or ""
    if cell.attrib.get("t") == "s":
        try:
            return shared[int(raw)]
        except Exception:
            return raw
    return raw


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    with zipfile.ZipFile(INPUT) as zf:
        names = zf.namelist()
        shared = _shared_strings(zf)
        workbook = _read_xml(zf, "xl/workbook.xml")
        sheets = []
        for sheet in workbook.findall("main:sheets/main:sheet", NS):
            sheets.append(dict(sheet.attrib))
        sheet_files = [name for name in names if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")]
        print(json.dumps({"file": str(INPUT), "zip_entries": len(names), "shared_string_count": len(shared), "sheets": sheets, "sheet_files": sheet_files}, ensure_ascii=False, indent=2))
        for sheet_file in sheet_files:
            root = _read_xml(zf, sheet_file)
            dimension = root.find("main:dimension", NS)
            rows = root.findall("main:sheetData/main:row", NS)
            cells = root.findall(".//main:c", NS)
            print("\n===", sheet_file, "===")
            print(json.dumps({"dimension": dimension.attrib.get("ref") if dimension is not None else "", "row_count": len(rows), "cell_count": len(cells)}, ensure_ascii=False))
            for row in rows[:20]:
                values = []
                for cell in row.findall("main:c", NS):
                    values.append({"ref": cell.attrib.get("r"), "type": cell.attrib.get("t", ""), "value": _cell_value(cell, shared)})
                print(json.dumps({"row": row.attrib.get("r"), "cells": values}, ensure_ascii=False))


if __name__ == "__main__":
    main()
