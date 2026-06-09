"""JSON parsing helpers for test generation postprocessing."""
from __future__ import annotations

import ast
import re
from json import JSONDecoder
from typing import Any


def _strip_to_json_start(text: str) -> tuple[str, bool]:
    first_array = text.find("[")
    first_obj = text.find("{")
    if first_array == -1 and first_obj == -1:
        raise ValueError("no json start")
    root_is_array = first_array != -1 and (first_obj == -1 or first_array < first_obj)
    start_idx = first_array if root_is_array else first_obj
    return text[start_idx:], root_is_array


def _remove_trailing_commas(text: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", text)


def _decode_concatenated_arrays(decoder: JSONDecoder, text: str, first: list[Any], end_idx: int) -> list[Any]:
    result = list(first)
    remaining = text[end_idx:]
    cursor = 0
    while cursor < len(remaining):
        next_bracket = remaining.find("[", cursor)
        if next_bracket == -1:
            break
        try:
            parsed, next_end = decoder.raw_decode(remaining[next_bracket:])
        except Exception:
            cursor = next_bracket + 1
            continue
        if isinstance(parsed, list):
            result.extend(parsed)
        cursor = next_bracket + next_end
    return result


def _decode_object_fragments(decoder: JSONDecoder, text: str) -> list[Any]:
    items: list[Any] = []
    cursor = 0
    while True:
        next_obj = text.find("{", cursor)
        if next_obj == -1:
            break
        try:
            obj, end_idx = decoder.raw_decode(text[next_obj:])
        except Exception:
            cursor = next_obj + 1
            continue
        items.append(obj)
        cursor = next_obj + end_idx
    return items


def clean_and_parse_json(response_text: str) -> Any:
    """Clean and parse model output into recoverable JSON."""
    cleaned_response = str(response_text or "")
    result: Any = None
    try:
        code_blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned_response)
        if code_blocks:
            cleaned_response = "\n".join(code_blocks)
        else:
            cleaned_response = cleaned_response.replace("```json", "").replace("```", "")

        cleaned_response = cleaned_response.replace("﻿", "").strip()
        cleaned_response, root_is_array = _strip_to_json_start(cleaned_response)
        cleaned_response = _remove_trailing_commas(cleaned_response)

        decoder = JSONDecoder()
        try:
            parsed, end_idx = decoder.raw_decode(cleaned_response)
            result = parsed
            if root_is_array and isinstance(result, list):
                result = _decode_concatenated_arrays(decoder, cleaned_response, result, end_idx)
        except Exception:
            if root_is_array:
                last_bracket = cleaned_response.rfind("]")
                if last_bracket != -1:
                    candidate = _remove_trailing_commas(cleaned_response[: last_bracket + 1])
                    try:
                        parsed, _ = decoder.raw_decode(candidate)
                        result = parsed
                    except Exception:
                        items = _decode_object_fragments(decoder, cleaned_response)
                        if items:
                            result = items
                        else:
                            raise
                else:
                    items = _decode_object_fragments(decoder, cleaned_response)
                    if items:
                        result = items
                    else:
                        raise
            else:
                last_brace = cleaned_response.rfind("}")
                if last_brace == -1:
                    raise
                candidate = _remove_trailing_commas(cleaned_response[: last_brace + 1])
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
