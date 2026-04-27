"""JSON parsing helpers for test generation postprocessing."""
from __future__ import annotations

import ast
import re
from json import JSONDecoder
from typing import Any

def clean_and_parse_json(response_text: str) -> Any:
    """
    娓呮礂骞惰В鏋愭ā鍨嬭繑鍥炴枃鏈紝灏介噺鎭㈠鎴愬彲鐢?JSON銆?

    璁捐鐩爣鏄€滃敖閲忔仮澶嶏紝涓嶈交鏄撳け璐モ€濓細
    1. 鍏煎 markdown 浠ｇ爜鍧椼€?
    2. 鍏煎澶氭 JSON 鏁扮粍鎷兼帴銆?
    3. 鍏煎灏鹃儴鎴柇銆佹湯灏鹃€楀彿绛夊父瑙佽剰鏁版嵁銆?
    4. 鏈€鍚庡厹搴曞埌 `ast.literal_eval`锛屼繚鎸佸巻鍙插閿欒涔夈€?
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
                # 涓枃娉ㄩ噴锛氳繖閲岄渶瑕佸蹇嶁€滄暟缁勪箣闂村す鏉傛棩蹇楀櫔澹扳€濈殑鍦烘櫙銆?
                # 鏃ч€昏緫閬囧埌绗竴涓棤娉曡В鏋愮殑 '[' 浼氱洿鎺?break锛屽鑷村悗缁湁鏁堟暟缁勪涪澶便€?
                # 鏂伴€昏緫鏀逛负鈥滄粦鍔ㄦ壂鎻忊€濓紝閬囧埌鍧忕墖娈靛氨鍓嶈繘 1 浣嶇户缁壘涓嬩竴涓?'['銆?
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
                                # 涓枃娉ㄩ噴锛氬璞℃仮澶嶆椂涓嶈鍥犱负鍗曚釜鑴忕墖娈电洿鎺ュ仠姝紝缁х画鍚戝悗鎵弿銆?
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
                            # 涓枃娉ㄩ噴锛氬璞℃仮澶嶆椂涓嶈鍥犱负鍗曚釜鑴忕墖娈电洿鎺ュ仠姝紝缁х画鍚戝悗鎵弿銆?
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
