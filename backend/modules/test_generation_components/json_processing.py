"""
测试用例 JSON 清洗与结构归一化组件。

该文件只负责处理模型输出的数据形态，不参与数据库、缓存或任务编排逻辑。
将其从主流程中拆出，是为了降低核心生成模块的复杂度并保持解析行为可复用。
"""

import ast
import re
from json import JSONDecoder
from typing import Any


def clean_and_parse_json(response_text: str) -> Any:
    """
    清洗并解析模型返回文本，尽量恢复成可用 JSON。

    设计目标是“尽量恢复，不轻易失败”：
    1. 兼容 markdown 代码块。
    2. 兼容多段 JSON 数组拼接。
    3. 兼容尾部截断、末尾逗号等常见脏数据。
    4. 最后兜底到 `ast.literal_eval`，保持历史容错语义。
    """
    cleaned_response = response_text
    result: Any = None
    try:
        code_blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned_response)
        if code_blocks:
            cleaned_response = "\n".join(code_blocks)
        else:
            cleaned_response = cleaned_response.replace("```json", "").replace("```", "")

        cleaned_response = cleaned_response.replace("\ufeff", "").strip()

        first_array = cleaned_response.find("[")
        first_obj = cleaned_response.find("{")
        if first_array == -1 and first_obj == -1:
            raise ValueError("no json start")

        root_is_array = first_array != -1 and (first_obj == -1 or first_array < first_obj)
        start_idx = first_array if root_is_array else first_obj
        cleaned_response = cleaned_response[start_idx:]

        cleaned_response = re.sub(r",\s*([}\]])", r"\1", cleaned_response)

        decoder = JSONDecoder()
        try:
            parsed, end_idx = decoder.raw_decode(cleaned_response)
            result = parsed

            if root_is_array and isinstance(result, list):
                # 中文注释：这里需要容忍“数组之间夹杂日志噪声”的场景。
                # 旧逻辑遇到第一个无法解析的 '[' 会直接 break，导致后续有效数组丢失。
                # 新逻辑改为“滑动扫描”，遇到坏片段就前进 1 位继续找下一个 '['。
                remaining = cleaned_response[end_idx:]
                cursor = 0
                while cursor < len(remaining):
                    try:
                        next_bracket = remaining.find("[", cursor)
                        if next_bracket == -1:
                            break
                        next_parsed, next_end = decoder.raw_decode(remaining[next_bracket:])
                        if isinstance(next_parsed, list):
                            result.extend(next_parsed)
                        cursor = next_bracket + next_end
                    except Exception:
                        cursor = next_bracket + 1
        except Exception:
            if root_is_array:
                last_bracket = cleaned_response.rfind("]")
                if last_bracket != -1:
                    candidate = cleaned_response[: last_bracket + 1]
                    candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
                    try:
                        parsed, _ = decoder.raw_decode(candidate)
                        result = parsed
                    except Exception:
                        items = []
                        cursor = 0
                        while True:
                            next_obj = cleaned_response.find("{", cursor)
                            if next_obj == -1:
                                break
                            try:
                                obj, end_idx = decoder.raw_decode(cleaned_response[next_obj:])
                                items.append(obj)
                                cursor = next_obj + end_idx
                            except Exception:
                                # 中文注释：对象恢复时不要因为单个脏片段直接停止，继续向后扫描。
                                cursor = next_obj + 1

                        if items:
                            result = items
                        else:
                            raise
                else:
                    items = []
                    cursor = 0
                    while True:
                        next_obj = cleaned_response.find("{", cursor)
                        if next_obj == -1:
                            break
                        try:
                            obj, end_idx = decoder.raw_decode(cleaned_response[next_obj:])
                            items.append(obj)
                            cursor = next_obj + end_idx
                        except Exception:
                            # 中文注释：对象恢复时不要因为单个脏片段直接停止，继续向后扫描。
                            cursor = next_obj + 1
                    if items:
                        result = items
                    else:
                        raise
            else:
                last_brace = cleaned_response.rfind("}")
                if last_brace == -1:
                    raise
                candidate = cleaned_response[: last_brace + 1]
                candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
                parsed, _ = decoder.raw_decode(candidate)
                result = parsed
    except Exception:
        try:
            if cleaned_response.strip().startswith(("[", "{")):
                eval_result = ast.literal_eval(cleaned_response)
                if isinstance(eval_result, (list, dict)):
                    result = eval_result
                else:
                    raise ValueError
            else:
                raise ValueError
        except Exception:
            result = {"error": "Failed to parse JSON", "raw_response": response_text}

    return result


def _normalize_for_dedup(text: Any) -> str:
    """中文注释：统一文本归一化，减少空白/大小写差异导致的重复误判。"""
    return str(text or "").strip().lower().replace("\r", "").replace("\n", " ")


def _case_dedup_key(case: dict[str, Any]) -> str:
    """
    中文注释：
    用“语义指纹”做去重，不依赖 id（id 可能因补齐重试出现重复或错位）。
    """
    module = _normalize_for_dedup(case.get("test_module"))
    desc = _normalize_for_dedup(case.get("description"))
    test_input = _normalize_for_dedup(case.get("test_input"))
    expected = _normalize_for_dedup(case.get("expected_result"))
    steps = case.get("steps") or []
    if isinstance(steps, list):
        steps_text = " | ".join(_normalize_for_dedup(s) for s in steps)
    else:
        steps_text = _normalize_for_dedup(steps)
    return f"{module}||{desc}||{test_input}||{expected}||{steps_text}"


def deduplicate_test_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """中文注释：保持原顺序去重，优先保留先出现的用例。"""
    if not isinstance(cases, list):
        return []
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        key = _case_dedup_key(case)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(case)
    return deduped


def count_unique_test_cases(cases: list[dict[str, Any]]) -> int:
    """中文注释：统一唯一用例计数口径，供补齐计算与日志展示复用。"""
    return len(deduplicate_test_cases(cases))


def normalize_json_structure(data: Any) -> Any:
    """
    将模型返回用例归一化为稳定字段结构，避免前端/导出链路处理不一致。

    注意：
    - 这里只做字段映射和格式标准化，不新增业务判定。
    - 当输入不是列表时保持原值返回，以维持历史错误透传行为。
    """
    if not isinstance(data, list):
        return data

    normalized = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue

        def pick(keys: list[str], default=None):
            for k in keys:
                if k in item and item.get(k) is not None:
                    return item.get(k)
            return default

        def normalize_list(v: Any) -> list[str]:
            if v is None:
                return []
            if isinstance(v, list):
                out: list[str] = []
                for x in v:
                    if isinstance(x, dict):
                        val = (
                            x.get("text")
                            or x.get("desc")
                            or x.get("step")
                            or x.get("name")
                            or x.get("内容")
                            or x.get("描述")
                            or x.get("步骤")
                        )
                        if val is not None:
                            out.append(str(val).strip())
                        else:
                            out.append(str(x).strip())
                    else:
                        out.append(str(x).strip())
                return [s for s in out if s]
            if isinstance(v, str):
                s = v.strip()
                if not s:
                    return []
                if "\n" in s:
                    return [line.strip() for line in s.splitlines() if line.strip()]
                if "；" in s:
                    return [seg.strip() for seg in s.split("；") if seg.strip()]
                if ";" in s:
                    return [seg.strip() for seg in s.split(";") if seg.strip()]
                return [s]
            return [str(v).strip()] if str(v).strip() else []

        raw_id = pick(
            ["id", "ID", "case_id", "caseId", "用例编号", "编号", "test_case_id", "testcase_id"],
            None,
        )
        raw_id_s = str(raw_id).strip() if raw_id is not None else ""
        if re.fullmatch(r"TC-\d{3,}", raw_id_s):
            final_id = raw_id_s
        elif re.fullmatch(r"\d+", raw_id_s):
            final_id = f"TC-{int(raw_id_s):03d}"
        else:
            final_id = f"TC-{i + 1:03d}"

        description = str(
            pick(["description", "desc", "用例描述", "描述", "name", "title", "标题"], "") or ""
        ).strip()
        test_module = str(
            pick(["test_module", "module", "testModule", "模块", "功能模块", "所属模块"], "") or ""
        ).strip()
        preconditions = normalize_list(
            pick(["preconditions", "precondition", "前置条件", "前提条件", "conditions"], [])
        )
        steps = normalize_list(pick(["steps", "step", "操作步骤", "步骤", "test_steps", "testSteps"], []))
        test_input = str(pick(["test_input", "input", "testInput", "输入", "测试输入", "入参"], "") or "").strip()
        expected_result = str(
            pick(["expected_result", "expected", "expectedResult", "预期结果", "期望结果", "断言"], "")
            or ""
        ).strip()
        priority = str(pick(["priority", "Priority", "prio", "优先级", "级别"], "P1") or "P1").strip()

        p = priority.upper()
        if p not in ["P0", "P1", "P2"]:
            if p in ["高", "HIGH"]:
                p = "P0"
            elif p in ["中", "MEDIUM"]:
                p = "P1"
            elif p in ["低", "LOW"]:
                p = "P2"
            else:
                p = "P1"

        normalized.append(
            {
                "id": final_id,
                "description": description,
                "test_module": test_module,
                "preconditions": preconditions,
                "steps": steps,
                "test_input": test_input,
                "expected_result": expected_result,
                "priority": p,
            }
        )

    return normalized
