"""
测试用例导出组件。

该组件负责把测试用例 JSON 结果转换为 Excel/CSV 二进制内容。
之所以拆出，是为了让主生成流程专注“生成与编排”，导出逻辑独立维护且可复用。
"""

import ast
import io
from typing import Any

import pandas as pd


def convert_json_to_excel(json_data: list | dict) -> bytes:
    """
    将测试用例 JSON 结构转换为 Excel 字节流。

    保持历史语义：
    - 输入为错误字典时导出单行错误信息。
    - 无法写 Excel 时自动回落为 CSV。
    """
    data: Any = json_data
    if isinstance(json_data, dict):
        if "error" in json_data:
            data = [{"error": json_data["error"]}]
        else:
            data = [json_data]

    if not isinstance(data, list):
        data = [data]

    processed_data = []
    for item in data:
        if not isinstance(item, dict):
            processed_data.append({"raw": str(item)})
            continue

        new_item = item.copy()

        pre = new_item.get("preconditions")
        if isinstance(pre, list):
            pre = [str(p).strip() for p in pre if str(p).strip()]
            new_item["preconditions"] = "\n".join(pre)
        elif isinstance(pre, str):
            if pre.strip().startswith("[") and pre.strip().endswith("]"):
                try:
                    val = ast.literal_eval(pre)
                    if isinstance(val, list):
                        val = [str(p).strip() for p in val if str(p).strip()]
                        new_item["preconditions"] = "\n".join(val)
                except Exception:
                    pass

        steps = new_item.get("steps")
        if isinstance(steps, list):
            steps = [str(s).strip() for s in steps if str(s).strip()]
            formatted_steps = [f"{i}. {s}" for i, s in enumerate(steps, 1)]
            new_item["steps"] = "\n".join(formatted_steps)
        elif isinstance(steps, str):
            if steps.strip().startswith("[") and steps.strip().endswith("]"):
                try:
                    val = ast.literal_eval(steps)
                    if isinstance(val, list):
                        val = [str(s).strip() for s in val if str(s).strip()]
                        formatted_steps = [f"{i}. {s}" for i, s in enumerate(val, 1)]
                        new_item["steps"] = "\n".join(formatted_steps)
                except Exception:
                    pass

        processed_data.append(new_item)

    df = pd.DataFrame(processed_data)

    try:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Test Cases")
        output.seek(0)
        return output.read()
    except Exception:
        return df.to_csv(index=False).encode("utf-8")

