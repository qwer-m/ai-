"""
智能测试生成引擎 (Intelligent Test Generation Engine)

此模块负责将用户需求（文本、图片OCR结果等）转化为结构化的测试用例。
核心功能：
1. 上下文压缩：针对超长需求文档或知识库，利用 LLM 进行智能摘要。
2. 批量生成：支持按批次生成大量用例，自动管理 ID 序列。
3. 流式生成：支持 Server-Sent Events (SSE) 风格的流式输出，提供实时进度反馈。
4. 格式清洗：强大的 JSON 修复能力，处理 LLM 返回的不规范 JSON。
5. 自动去重：在追加模式下，通过历史记录防止用例重复。

依赖：
- core.ai_client: 模型调用。
- modules.knowledge_base: RAG 检索支持。
"""

from core.ai_client import ai_client, get_client_for_user
from sqlalchemy.orm import Session
from core.models import TestGeneration, LogEntry
from modules.knowledge_base import knowledge_base
from core.config import settings
import json
import pandas as pd
import io
import re
from json import JSONDecoder
import ast

def clean_and_parse_json(response_text: str) -> any:
    """
    清洗并解析 LLM 返回的 JSON 文本 (Clean and Parse JSON Response)
    
    LLM 返回的 JSON 经常存在格式问题，此函数尝试多种策略进行修复：
    1. 提取 Markdown 代码块 (```json ... ```)。
    2. 去除无关的前后缀文本。
    3. 处理拼接的 JSON 数组 (应对流式拼接场景)。
    4. 修复未闭合的数组或对象 (应对截断场景)。
    5. 兜底策略：使用 ast.literal_eval 尝试解析 Python 字面量格式。
    """
    cleaned_response = response_text
    result = None
    try:
        # Improved Markdown extraction using regex - Find ALL blocks to support batches
        # (改进的 Markdown 提取 - 查找所有代码块以支持批处理)
        code_blocks = re.findall(r'```(?:json)?\s*([\s\S]*?)\s*```', cleaned_response)
        if code_blocks:
            cleaned_response = "\n".join(code_blocks)
        else:
            # If no blocks found, strip potential backticks from raw text
            # (如果没有找到代码块，去除原始文本中可能存在的反引号)
            cleaned_response = cleaned_response.replace("```json", "").replace("```", "")
        
        cleaned_response = cleaned_response.replace("\ufeff", "").strip()

        first_array = cleaned_response.find("[")
        first_obj = cleaned_response.find("{")
        if first_array == -1 and first_obj == -1:
            raise ValueError("no json start")

        root_is_array = (first_array != -1 and (first_obj == -1 or first_array < first_obj))
        start_idx = first_array if root_is_array else first_obj
        cleaned_response = cleaned_response[start_idx:]

        cleaned_response = re.sub(r",\s*([}\]])", r"\1", cleaned_response)

        decoder = JSONDecoder()
        try:
            parsed, end_idx = decoder.raw_decode(cleaned_response)
            result = parsed
            
            # Support multiple JSON arrays concatenated (e.g. from streaming chunks)
            # (支持拼接的多个 JSON 数组，例如来自流式分块的数据)
            if root_is_array and isinstance(result, list):
                remaining = cleaned_response[end_idx:].strip()
                while remaining:
                    try:
                        # Skip potential garbage/separators until next '['
                        # (跳过可能的垃圾字符/分隔符，直到下一个 '[')
                        if not remaining.startswith("["):
                            next_bracket = remaining.find("[")
                            if next_bracket != -1:
                                remaining = remaining[next_bracket:]
                            else:
                                break
                        
                        next_parsed, next_end = decoder.raw_decode(remaining)
                        if isinstance(next_parsed, list):
                            result.extend(next_parsed)
                        remaining = remaining[next_end:].strip()
                    except Exception:
                        break
        except Exception:
            if root_is_array:
                # 尝试修复未闭合的数组：先找最后一个闭合的括号 ]
                # (Try to fix unclosed array: find the last closing bracket ']')
                last_bracket = cleaned_response.rfind("]")
                if last_bracket != -1:
                    candidate = cleaned_response[: last_bracket + 1]
                    candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
                    try:
                        parsed, _ = decoder.raw_decode(candidate)
                        result = parsed
                    except Exception:
                        # 如果还是失败，可能是中间有错，尝试逐个对象解析
                        # (If still fails, try parsing objects one by one)
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
                                break
                        
                        if items:
                            result = items
                        else:
                            raise
                else:
                    # 根本没有 ]，说明完全截断，尝试逐个提取对象
                    # (No ']' found, meaning complete truncation, try extracting objects one by one)
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
                            break
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
        # Fallback: try ast.literal_eval
        try:
            if cleaned_response.strip().startswith(('[', '{')):
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

def normalize_json_structure(data: any) -> any:
    """
    Enforce strict JSON structure for test cases.
    Each item must be a dict with specific keys.
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

        def normalize_list(v: any) -> list[str]:
            if v is None:
                return []
            if isinstance(v, list):
                out: list[str] = []
                for x in v:
                    if isinstance(x, dict):
                        val = x.get("text") or x.get("desc") or x.get("step") or x.get("name") or x.get("内容") or x.get("描述") or x.get("步骤")
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

        raw_id = pick(["id", "ID", "case_id", "caseId", "用例编号", "编号", "test_case_id", "testcase_id"], None)
        raw_id_s = str(raw_id).strip() if raw_id is not None else ""
        if re.fullmatch(r"TC-\d{3,}", raw_id_s):
            final_id = raw_id_s
        elif re.fullmatch(r"\d+", raw_id_s):
            final_id = f"TC-{int(raw_id_s):03d}"
        else:
            final_id = f"TC-{i + 1:03d}"

        description = str(pick(["description", "desc", "用例描述", "描述", "name", "title", "标题"], "") or "").strip()
        test_module = str(pick(["test_module", "module", "testModule", "模块", "功能模块", "所属模块"], "") or "").strip()
        preconditions = normalize_list(pick(["preconditions", "precondition", "前置条件", "前提条件", "conditions"], []))
        steps = normalize_list(pick(["steps", "step", "操作步骤", "步骤", "test_steps", "testSteps"], []))
        test_input = str(pick(["test_input", "input", "testInput", "输入", "测试输入", "入参"], "") or "").strip()
        expected_result = str(pick(["expected_result", "expected", "expectedResult", "预期结果", "期望结果", "断言"], "") or "").strip()
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

        new_item = {
            "id": final_id,
            "description": description,
            "test_module": test_module,
            "preconditions": preconditions,
            "steps": steps,
            "test_input": test_input,
            "expected_result": expected_result,
            "priority": p
        }

        normalized.append(new_item)
        
    return normalized

from starlette.concurrency import run_in_threadpool
import asyncio

class TestGenerationModule:
    """
    测试生成模块核心类 (Test Generation Logic)
    封装了用例数量估算、JSON 生成、流式生成等核心业务逻辑。
    """
    def __init__(self):
        pass

    def estimate_test_count(self, requirement: str, project_id: int, db: Session, user_id: int = None) -> int:
        """
        估算测试用例数量 (Estimate Test Case Count)
        利用 LLM 根据需求长度和复杂度，快速估算合理的用例数量，用于前端进度条或默认值设置。
        """
        try:
            client = get_client_for_user(user_id, db)
            
            # Simple RAG retrieval for context
            query_text = requirement[:500] if requirement else ""
            kb_context = ""
            try:
                kb_context = knowledge_base.get_relevant_context(query=query_text, project_id=project_id, limit=2, db=db, user_id=user_id)
            except Exception:
                pass
            
            doc_len = len(requirement) if requirement else 0
            
            system_prompt = f"""
            You are an expert QA lead.
            Based on the requirement scale and project context provided by the user, ESTIMATE the reasonable number of test cases needed to cover the ESSENTIAL functionality.
            
            Project Context (Reference):
            {kb_context}
            
            Document Statistics:
            - Total Length: {doc_len} characters
            
            Rules:
            1. Return ONLY a single integer number (e.g. 15).
            2. Do not return a range (e.g. 10-20).
            3. Do not return any text explanation.
            4. Be EFFICIENT but COMPREHENSIVE. 
               - Cover Critical and Major paths thoroughly.
               - Include necessary edge cases and negative tests.
               - Avoid redundant permutations, but ensure full logic coverage.
            5. Scaling Guide:
               - Simple Login/Reset Password: 5-8 cases.
               - CRUD Management Page: 10-15 cases.
               - Complex Form/Process: 20-30 cases.
            6. The goal is a Standard Regression Suite.
            """
            
            user_msg = f"Requirement Content (first 2000 chars):\n{requirement[:2000]}"
            
            response = client.generate_response(user_msg, system_prompt, db=db)
            
            # Parse integer
            text_resp = str(response).strip()
            match = re.search(r'\d+', text_resp)
            if match:
                val = int(match.group(0))
                # Apply a mild damping factor (approx -10%) to prevent slight inflation
                val = int(val * 0.9)
                # Safety bounds - moderate max cap
                return max(5, min(val, 100))
            return 20
        except Exception as e:
            print(f"Estimation failed: {e}")
            raise e  # Propagate error to let frontend handle it, no fallback guessing

    def generate_test_cases_json(self, requirement: str, project_id: int, db: Session = None, doc_type: str = "requirement", compress: bool = False, expected_count: int = 20, batch_size: int = 20, batch_index: int = 0, user_id: int = None) -> dict:
        """
        生成测试用例 - JSON 格式 (Generate Test Cases JSON)
        
        Args:
            requirement: 需求文本。
            project_id: 项目ID，用于 RAG 检索上下文。
            db: 数据库会话。
            doc_type: 文档类型 (requirement, prototype, incomplete)。不同的类型会触发不同的 Prompt 策略。
            compress: 是否启用上下文压缩（针对超长文本）。
            expected_count: 预期总数量。
            batch_size: 当前批次大小。
            batch_index: 当前批次索引 (用于计算 ID 起始值)。
            user_id: 用户ID，用于获取特定的 AI 模型配置。
            
        Returns:
            dict: 包含生成的用例列表，或者错误信息。
        """
        # Get client for user
        client = get_client_for_user(user_id, db)

        # Retrieve context from Knowledge Base if DB is available
        original_requirement = requirement
        kb_context = ""
        if db:
            if compress:
                # If compression is enabled, we try to summarize the global context
                full_context = knowledge_base.get_all_context(db, project_id, user_id=user_id)
                kb_context = client.compress_context(
                    full_context,
                    prompt="请将以下项目知识压缩为适合测试用例生成的精炼摘要，保留关键实体、流程、约束、字段、边界与异常规则。输出纯文本。",
                    db=db
                ) if full_context else ""
                
                # Also compress requirement if needed
                try:
                    requirement = client.compress_context(
                        requirement,
                        prompt="请将以下需求压缩为适合测试用例生成的精炼版本，保留技术细节、字段/ID、约束、边界与异常规则，去除冗余。输出纯文本。",
                        db=db
                    )
                except Exception:
                    pass
            else:
                # Use RAG to retrieve relevant context based on requirement
                # Use first 1000 chars of requirement as query to find relevant knowledge
                query_text = requirement[:1000] if requirement else ""
                kb_context = knowledge_base.get_relevant_context(query=query_text, project_id=project_id, limit=5, db=db, user_id=user_id)

        base_prompt = """You are an expert QA engineer.
Generate test cases in STRICT JSON format.
You MUST apply the following testing techniques:
1. Equivalence Partitioning (等价类划分): Cover both valid and invalid equivalence classes.
2. Boundary Value Analysis (边界值分析): Test boundaries (min, max, just below min, just above max) for numeric or range-based inputs.

IMPORTANT LANGUAGE REQUIREMENT:
All content (description, steps, test_input, expected_result, preconditions, test_module) MUST be in Chinese (Simplified).
Do not output English unless it is a specific technical term or variable name from the requirement.

STRICT OUTPUT REQUIREMENTS (MANDATORY):
- Output MUST be a single valid JSON array (no extra text before/after).
- Do NOT output Markdown, code fences, explanations, or batch headers.
- Each array item MUST be a JSON object with EXACT keys:
  id, description, test_module, preconditions, steps, test_input, expected_result, priority
- No additional keys are allowed.
- Types:
  - id: string like "TC-001"
  - description: string
  - test_module: string
  - preconditions: array of strings (can be empty [])
  - steps: array of strings (must be non-empty)
  - test_input: string
  - expected_result: string
  - priority: one of "P0","P1","P2"
"""
        
        if doc_type == "prototype":
            base_prompt += """
            The input provided is a description of a UI prototype (derived from an image).
            Focus on testing the UI elements, layout, user interactions, and visual states described.
            Infer expected behaviors for buttons, inputs, and navigation based on standard UI patterns.
            """
        elif doc_type == "incomplete":
            base_prompt += """
            The input provided is an incomplete requirement document.
            You should:
            1. Generate test cases for the parts that are clearly defined.
            2. For missing or ambiguous information, infer reasonable expected results based on common software standards.
            3. Add a tag "[Pending Confirmation]" to the description of test cases that rely on inferred information.
            """
        
        # Calculate start number for IDs based on batch index
        start_id = batch_index * batch_size + 1
        
        system_prompt = f"""
        {base_prompt}
        
        Reference Knowledge (Use this style/info if relevant):
        {kb_context}
        
        The JSON should be a list of objects with keys: id, description, test_module, preconditions, steps, test_input, expected_result, priority.
        - test_module: Explain which area/module this test case belongs to (e.g., Login, User Management, Payment).
        - test_input: Describe the input actions or data changes in the steps. Explicitly mention if a value is a Boundary Value or Invalid Equivalence Class.
        - description: Include the specific scenario being tested (e.g., "Verify login with empty password" or "Verify age input at boundary 18").
        
        BATCH GENERATION INSTRUCTION:
        This is batch #{batch_index + 1}. 
        Generate exactly {batch_size} test cases.
        Start the Test Case IDs from {start_id} (e.g., TC-{start_id:03d}).
        Ensure these test cases cover different aspects or scenarios than previous batches if possible, or just proceed sequentially through the requirement logic.
        
        Return ONLY the JSON list.
        """
        response = client.generate_response(requirement, system_prompt, db=db)
        
        # ... rest of function using response ...
        if isinstance(response, (list, dict)):
            result = response
        else:
            result = clean_and_parse_json(response)
            
        # Save to DB if session provided
        if db:
            try:
                db_entry = TestGeneration(
                    requirement_text=original_requirement,
                    generated_result=json.dumps(result, ensure_ascii=False) if not "error" in result else json.dumps({"error": result, "raw": response}, ensure_ascii=False),
                    project_id=project_id,
                    user_id=user_id
                )
                db.add(db_entry)
                db.commit()
                db.refresh(db_entry)
                # Add db id to result for reference
                if isinstance(result, list):
                     pass # Can't add to list easily, maybe wrap? keeping as is.
                elif isinstance(result, dict):
                    result['db_id'] = db_entry.id
            except Exception as e:
                print(f"Failed to save to DB: {e}")
                
        return result
    
    def generate_test_cases_stream(self, requirement: str, project_id: int, db: Session = None, doc_type: str = "requirement", compress: bool = False, expected_count: int = 20, batch_size: int = 10, overwrite: bool = False, append: bool = False, user_id: int = None):
        # Get client for user
        client = get_client_for_user(user_id, db)

        # Retrieve context from Knowledge Base if DB is available
        original_requirement = requirement
        kb_context = ""
        
        # Determine start_id if appending
        start_id = 1
        existing_cases = []
        existing_entry = None
        
        if db and append:
             from sqlalchemy import desc
             query = db.query(TestGeneration).filter(
                 TestGeneration.project_id == project_id,
                 TestGeneration.requirement_text == original_requirement
             )
             if user_id:
                 query = query.filter(TestGeneration.user_id == user_id)
             existing_entry = query.order_by(desc(TestGeneration.created_at)).first()
             
             if existing_entry and existing_entry.generated_result:
                 try:
                     existing_cases = json.loads(existing_entry.generated_result)
                     if isinstance(existing_cases, list):
                         start_id = len(existing_cases) + 1
                 except Exception:
                     pass

        if db:
            if compress:
                yield "@@STATUS@@:正在进行智能上下文压缩及知识库检索，这可能需要几十秒，请耐心等待...\n"
                full_context = knowledge_base.get_all_context(db, project_id, user_id=user_id)
                
                kb_compression_success = False
                if full_context:
                    try:
                        # Attempt to compress full context
                        kb_summary = client.compress_context(
                            full_context,
                            prompt="请将以下项目知识压缩为适合测试用例生成的精炼摘要，保留关键实体、流程、约束、字段、边界与异常规则。输出纯文本。",
                            db=db
                        )
                        
                        if kb_summary and not kb_summary.startswith("Error") and not kb_summary.startswith("Exception"):
                            kb_context = kb_summary
                            kb_compression_success = True
                            yield f"@@STATUS@@:知识库压缩完成 ({len(full_context)} -> {len(kb_context)} 字符)...\n"
                        else:
                            yield f"@@STATUS@@:知识库压缩返回异常 ({kb_summary[:50]}...)，转为使用RAG检索...\n"
                    except Exception as e:
                        yield f"@@STATUS@@:知识库压缩失败 ({str(e)})，转为使用RAG检索...\n"
                
                # Fallback to RAG if compression failed or no context
                if not kb_compression_success:
                     query_text = requirement[:1000] if requirement else ""
                     kb_context = knowledge_base.get_relevant_context(query=query_text, project_id=project_id, limit=10, db=db, user_id=user_id)
                     if kb_context:
                         yield f"@@STATUS@@:已通过RAG检索相关知识 ({len(kb_context)} 字符)...\n"

                try:
                    req_len_before = len(requirement)
                    compressed_req = client.compress_context(
                        requirement,
                        prompt="请将以下需求压缩为适合测试用例生成的精炼版本，保留技术细节、字段/ID、约束、边界与异常规则，去除冗余。输出纯文本。",
                        db=db
                    )
                    # Check if compression actually worked (not error message)
                    if compressed_req and not compressed_req.startswith("Error"):
                        requirement = compressed_req
                        yield f"@@STATUS@@:需求压缩完成 ({req_len_before} -> {len(requirement)} 字符)...\n"
                    else:
                        yield f"@@STATUS@@:需求压缩返回异常，使用原始文本: {compressed_req[:50]}...\n"
                except Exception as e:
                    yield f"@@STATUS@@:需求压缩失败 ({str(e)})，将使用原始文本...\n"
                    pass
            else:
                query_text = requirement[:1000] if requirement else ""
                kb_context = knowledge_base.get_relevant_context(query=query_text, project_id=project_id, limit=5, db=db, user_id=user_id)

        if db and not compress:
            if requirement and len(requirement) > 120000:
                yield "@@STATUS@@:输入内容较长，正在进行智能压缩以适配模型上下文...\n"
                try:
                    req_len_before = len(requirement)
                    compressed_req = client.compress_context(
                        requirement,
                        prompt="请将以下需求压缩为适合测试用例生成的精炼版本，保留技术细节、字段/ID、约束、边界与异常规则，去除冗余。输出纯文本。",
                        db=db
                    )
                    if compressed_req and not compressed_req.startswith("Error"):
                        requirement = compressed_req
                        yield f"@@STATUS@@:长文本压缩完成 ({req_len_before} -> {len(requirement)} 字符)...\n"
                    else:
                        yield f"@@STATUS@@:长文本压缩异常，使用原始文本...\n"
                except Exception:
                    pass
            if kb_context and len(kb_context) > 120000:
                yield "@@STATUS@@:知识库上下文较长，正在进行智能压缩以适配模型上下文...\n"
                try:
                    kb_len_before = len(kb_context)
                    compressed_kb = client.compress_context(
                        kb_context,
                        prompt="请将以下检索到的知识库上下文压缩为适合测试用例生成的精炼摘要，保留关键实体、流程、约束、字段、边界与异常规则。输出纯文本。",
                        db=db
                    )
                    if compressed_kb and not compressed_kb.startswith("Error"):
                        kb_context = compressed_kb
                        yield f"@@STATUS@@:知识库压缩完成 ({kb_len_before} -> {len(kb_context)} 字符)...\n"
                    else:
                        yield f"@@STATUS@@:知识库压缩异常，使用原始文本...\n"
                except Exception:
                    pass

        base_prompt = """You are an expert QA engineer.
Generate test cases in STRICT JSON format.
You MUST apply the following testing techniques:
1. Equivalence Partitioning (等价类划分): Cover both valid and invalid equivalence classes.
2. Boundary Value Analysis (边界值分析): Test boundaries (min, max, just below min, just above max) for numeric or range-based inputs.

PRIORITY INSTRUCTION:
- Prioritize Edge cases (Boundary) and Negative cases (Error/Fail) over Positive cases (Default/Happy Path).
- Ensure a good mix of P0 (Critical), P1 (Major), and P2 (Minor) cases.

IMPORTANT LANGUAGE REQUIREMENT:
All content (description, steps, test_input, expected_result, preconditions, test_module) MUST be in Chinese (Simplified).
Do not output English unless it is a specific technical term or variable name from the requirement.

STRICT OUTPUT REQUIREMENTS (MANDATORY):
- Output MUST be a single valid JSON array (no extra text before/after).
- Format the JSON with indentation (2 spaces) and newlines for readability.
- Do NOT output Markdown, code fences, explanations, or batch headers.
- Each array item MUST be a JSON object with EXACT keys:
  id, description, test_module, preconditions, steps, test_input, expected_result, priority
- No additional keys are allowed.
- Do NOT wrap the JSON array in an object (e.g., {"test_cases": [...]}).
- preconditions and steps MUST be arrays of strings (not single strings).

JSON STRUCTURE EXAMPLE (Follow this structure exactly):
[
   { 
     "id": "TC-001", 
     "description": "验证内部试用机注册时未获取GPS经纬度，应禁止保存", 
     "test_module": "内部试用机申请", 
     "preconditions": [ 
       "系统已登录具备试用机申请权限的销售账号", 
       "设备GPS功能被禁用或模拟无定位" 
     ], 
     "steps": [ 
       "进入内部试用机申请页", 
       "不填写任何经纬度信息", 
       "点击提交按钮" 
     ], 
     "test_input": "经度为空，纬度为空", 
     "expected_result": "提示'请成功获取设备位置信息后提交'，表单无法提交", 
     "priority": "P0" 
   }
]

Types:
  - id: string like "TC-001"
  - description: string
  - test_module: string
  - preconditions: array of strings (can be empty [])
  - steps: array of strings (must be non-empty)
  - test_input: string
  - expected_result: string
  - priority: one of "P0","P1","P2"
"""
        
        if doc_type == "prototype":
            base_prompt += """
            The input provided is a description of a UI prototype (derived from an image).
            Focus on testing the UI elements, layout, user interactions, and visual states described.
            Infer expected behaviors for buttons, inputs, and navigation based on standard UI patterns.
            """
        elif doc_type == "incomplete":
            base_prompt += """
            The input provided is an incomplete requirement document.
            You should:
            1. Generate test cases for the parts that are clearly defined.
            2. For missing or ambiguous information, infer reasonable expected results based on common software standards.
            3. Add a tag "[Pending Confirmation]" to the description of test cases that rely on inferred information.
            """
        
        full_content = ""
        
        # Calculate batches
        import math

        # Dynamic Batch Size Adjustment based on User Request
        current_existing_count = len(existing_cases) if isinstance(existing_cases, list) else 0
        
        if append:
            needed_to_append = expected_count - current_existing_count
            if needed_to_append > 25:
                batch_size = 25
            else:
                # If needed is small (e.g. 5), we generate all in one batch
                batch_size = max(1, needed_to_append)
        else:
            # For fresh generation, user requested 25 per batch
            batch_size = 25

        # Ensure batch_size is at least 1 to avoid infinite loop
        batch_size = max(1, batch_size)
        
        # Handle Append Mode: If expected_count is met, auto-increment
        current_count = len(existing_cases)
        if append and expected_count <= current_count:
            yield f"@@STATUS@@:当前用例数({current_count})已达预期({expected_count})，自动增加 {batch_size} 条用例...\n"
            expected_count = current_count + batch_size

        total_batches = math.ceil((expected_count - (start_id - 1)) / batch_size)
        # Ensure at least 1 batch if needed
        if total_batches < 1 and expected_count > (start_id - 1):
            total_batches = 1
        
        current_id = start_id
        
        # History tracking for de-duplication
        history_summaries = []
        if append and isinstance(existing_cases, list):
            for c in existing_cases:
                if isinstance(c, dict):
                    history_summaries.append(f"{c.get('id', '')}: {c.get('description', '')}")

        for i in range(total_batches):
            remaining = expected_count - (current_id - start_id)
            current_batch_count = min(batch_size, remaining)
            
            if current_batch_count <= 0:
                break

            generated_in_batch = 0
            attempt = 0
            batch_content = ""

            while generated_in_batch < current_batch_count and attempt < 3:
                need = current_batch_count - generated_in_batch
                attempt += 1
                yield f"@@STATUS@@:正在生成第 {i+1}/{total_batches} 批次 ({current_batch_count} 条) - 第 {attempt} 次尝试...\n"

                # Build history context (last 50 items to save tokens)
                history_context_str = ""
                if history_summaries:
                    recent_history = history_summaries[-50:]
                    history_list_str = "\n".join([f"- {h}" for h in recent_history])
                    history_context_str = f"""
                    IMPORTANT - DE-DUPLICATION INSTRUCTION:
                    The following test scenarios have ALREADY been generated. 
                    DO NOT generate duplicates or very similar cases to these:
                    {history_list_str}
                    
                    Focus on NEW scenarios, different edge cases, or other modules.
                    """

                system_prompt = f"""
                {base_prompt}
                
                Reference Knowledge (Use this style/info if relevant):
                {kb_context}
                
                {history_context_str}
                
                BATCH GENERATION INSTRUCTION:
                This is batch {i+1} of {total_batches}.
                Generate exactly {need} test cases.
                Start the Test Case IDs from {current_id + generated_in_batch} (e.g., TC-{(current_id + generated_in_batch):03d}).
                Ensure the list length is exactly {need}.
                
                Return ONLY the JSON array.
                """

                stream = client.generate_response_stream(requirement, system_prompt)
                chunk_acc = ""
                provider_error = None
                for chunk in stream:
                    chunk_acc += chunk
                    full_content += chunk
                    batch_content += chunk
                    yield chunk # Stream chunk directly for better performance
                    if chunk.startswith("Error:") or chunk.startswith("[额度耗尽]") or chunk.startswith("Exception occurred:"):
                        provider_error = chunk
                        break

                if not provider_error and not chunk_acc.strip():
                    if attempt < 3:
                        yield "\n@@STATUS@@:模型未返回内容，正在重试...\n"
                        continue
                    yield "\n@@STATUS@@:生成失败\n"
                    yield "Error: 模型未返回内容（可能是模型配置/额度/网络/内容安全导致），请检查后重试\n"
                    attempt = 3
                    break

                if provider_error:
                    yield "\n@@STATUS@@:生成失败\n"
                    yield f"{provider_error}\n"
                    attempt = 3
                    break

                full_content += "\n"
                batch_content += "\n"
                yield "\n"

                try:
                    parsed_batch = clean_and_parse_json(batch_content)
                    parsed_batch = normalize_json_structure(parsed_batch)
                    if isinstance(parsed_batch, list):
                        generated_in_batch = len(parsed_batch)
                        # Update history for next batch/retry
                        for case in parsed_batch:
                            if isinstance(case, dict):
                                history_summaries.append(f"{case.get('id', '')}: {case.get('description', '')}")
                except Exception:
                    pass

            current_id += current_batch_count

        # Post-processing and saving to DB after stream finishes
        try:
            # Try to clean and parse the full content to ensure it's valid JSON before saving
            parsed_result = clean_and_parse_json(full_content)
            # Enforce standard structure
            parsed_result = normalize_json_structure(parsed_result)

            # Calculate total count including existing cases if in append mode
            current_total = len(parsed_result) if isinstance(parsed_result, list) else 0
            if append and isinstance(existing_cases, list):
                current_total += len(existing_cases)

            if isinstance(parsed_result, list) and expected_count:
                # Truncate if we have too many (to respect "exact" count and avoid confusion)
                # But only if it's significantly more (e.g. > 5 extra) to avoid cutting off a good case slightly?
                # Actually, user wants exact control. Let's truncate to expected_count if we are sure.
                # But wait, expected_count is the target.
                
                # Logic to supplement
                current_total = len(parsed_result)
                if append and isinstance(existing_cases, list):
                    current_total += len(existing_cases)

                if current_total < expected_count:
                    missing = expected_count - current_total
                    supplement_attempt = 0
                    while missing > 0 and supplement_attempt < 3:
                        supplement_attempt += 1
                        yield f"@@STATUS@@:检测到缺少 {missing} 条用例，正在补齐(第 {supplement_attempt} 次)...\n"
                        system_prompt = f"""
                        {base_prompt}

                        Reference Knowledge (Use this style/info if relevant):
                        {kb_context}

                        SUPPLEMENT INSTRUCTION:
                        Generate exactly {missing} additional test cases.
                        Start the Test Case IDs from {current_total + 1} (e.g., TC-{(current_total + 1):03d}).
                        Return ONLY the JSON array.
                        """
                        extra_content = ""
                        extra_stream = client.generate_response_stream(requirement, system_prompt)
                        provider_error = None
                        for chunk in extra_stream:
                            extra_content += chunk
                            full_content += chunk
                            yield chunk
                            if chunk.startswith("Error:") or chunk.startswith("[额度耗尽]") or chunk.startswith("Exception occurred:"):
                                provider_error = chunk
                                break
                        if provider_error:
                            yield "\n@@STATUS@@:生成失败\n"
                            yield f"{provider_error}\n"
                            break
                        full_content += "\n"
                        yield "\n"
                        try:
                            extra_parsed = clean_and_parse_json(extra_content)
                            extra_parsed = normalize_json_structure(extra_parsed)
                            if isinstance(extra_parsed, list) and extra_parsed:
                                parsed_result.extend(extra_parsed)
                                parsed_result = normalize_json_structure(parsed_result)
                                # Update current total
                                current_total = len(parsed_result)
                                if append and isinstance(existing_cases, list):
                                    current_total += len(existing_cases)
                        except Exception:
                            pass
                        missing = expected_count - current_total

                # Final Truncation if exceeded
                if current_total > expected_count:
                    # Calculate how many to keep
                    # We only truncate the NEW generated ones. We don't touch existing cases.
                    # current_total = len(parsed_result) + len(existing_cases)
                    # We want current_total == expected_count
                    # So len(parsed_result) should be expected_count - len(existing_cases)
                    
                    target_new_count = expected_count
                    if append and isinstance(existing_cases, list):
                        target_new_count = expected_count - len(existing_cases)
                    
                    if target_new_count < 0: target_new_count = 0
                    
                    if len(parsed_result) > target_new_count:
                         parsed_result = parsed_result[:target_new_count]
                         yield f"@@STATUS@@:已生成 {current_total} 条，超出预期的 {expected_count} 条，自动截取前 {expected_count} 条。\n"


            if isinstance(parsed_result, dict) and parsed_result.get("error"):
                yield "\n@@STATUS@@:生成失败\n"
                yield f"Error: {parsed_result.get('error')}\n"
            elif isinstance(parsed_result, list) and len(parsed_result) == 0:
                yield "\n@@STATUS@@:生成失败\n"
                yield "Error: 模型返回空数组或解析不到有效用例，请检查模型配置/提示词/网络后重试\n"

            cleaned_response = json.dumps(parsed_result, ensure_ascii=False)
            
            if db:
                if overwrite:
                    from sqlalchemy import desc
                    query = db.query(TestGeneration).filter(
                        TestGeneration.project_id == project_id,
                        TestGeneration.requirement_text == original_requirement
                    )
                    if user_id:
                        query = query.filter(TestGeneration.user_id == user_id)
                    existing_entry_overwrite = query.order_by(desc(TestGeneration.created_at)).first()
                    
                    if existing_entry_overwrite:
                        existing_entry_overwrite.generated_result = cleaned_response
                        db.commit()
                        db.refresh(existing_entry_overwrite)
                    else:
                         db_entry = TestGeneration(
                            requirement_text=original_requirement,
                            generated_result=cleaned_response, # Save the cleaned text which should be JSON
                            project_id=project_id,
                            user_id=user_id
                        )
                         db.add(db_entry)
                         db.commit()
                elif append and existing_entry:
                    # Merge with existing cases
                    if isinstance(parsed_result, list):
                        merged_result = existing_cases + parsed_result
                        existing_entry.generated_result = json.dumps(merged_result, ensure_ascii=False)
                        db.commit()
                        db.refresh(existing_entry)
                else:
                    db_entry = TestGeneration(
                        requirement_text=original_requirement,
                        generated_result=cleaned_response, # Save the cleaned text which should be JSON
                        project_id=project_id,
                        user_id=user_id
                    )
                    db.add(db_entry)
                    db.commit()

                # --- Log GEN_DIAG and GEN_QM ---
                try:
                    count = len(parsed_result) if isinstance(parsed_result, list) else 0
                    
                    # Calculate actual model for accurate logging
                    # system_prompt is defined above in this function
                    full_input = (system_prompt or "") + requirement
                    actual_model = client.select_model(full_input)
                    
                    # GEN_DIAG
                    diag = {
                        "kind": "gen_diag",
                        "mode": "stream",
                        "doc_type": doc_type,
                        "compress": compress,
                        "expected_count": expected_count,
                        "generated_count": count,
                        "content_length": len(requirement),
                        "kb_length": len(kb_context or ""),
                        "prototype_included": "[Prototype Analysis]" in requirement,
                        "model": actual_model,  # Use actual selected model
                        "max_tokens": client.max_tokens
                    }
                    
                    db.add(LogEntry(
                        project_id=project_id,
                        log_type="system",
                        message=f"GEN_DIAG:{json.dumps(diag, ensure_ascii=False)}",
                        user_id=user_id
                    ))
                    
                    # GEN_QM
                    positive = 0
                    negative = 0
                    edge = 0
                    avg_steps = 0.0
                    pending = 0
                    steps_count = 0
                    steps_items = 0
                    kw_neg = ["失败", "错误", "异常", "不可用", "拒绝", "超时", "Invalid", "Fail", "Error", "Exception", "Timeout", "Deny"]
                    kw_edge = ["边界", "最大值", "最小值", "极限", "临界", "空值", "重复", "特殊字符", "溢出", "Boundary", "Edge", "Max", "Min", "Limit", "Critical", "Null", "Empty", "Overflow"]
                    
                    if isinstance(parsed_result, list):
                        for item in parsed_result:
                            # Combine fields for keyword search
                            desc = (item.get("description") or "") + " " + \
                                   (item.get("expected_result") or "") + " " + \
                                   (item.get("test_input") or "")
                            
                            # Add steps to search text
                            steps_text = ""
                            steps = item.get("steps")
                            if isinstance(steps, list):
                                steps_text = " ".join(str(s) for s in steps)
                            elif isinstance(steps, str):
                                steps_text = steps
                            
                            search_text = (desc + " " + steps_text).lower() # Use lowercase for case-insensitive search
                            
                            # Check keywords (case-insensitive)
                            is_neg = any(k.lower() in search_text for k in kw_neg)
                            is_edge = any(k.lower() in search_text for k in kw_edge)
                            
                            # Priority: Edge > Negative > Positive
                            # (Or as per user request to "re-plan", we ensure mutually exclusive or correct classification)
                            # Current Logic:
                            # If Edge keywords found -> Edge
                            # Else if Negative keywords found -> Negative
                            # Else -> Positive
                            
                            if is_edge:
                                edge += 1
                            elif is_neg:
                                negative += 1
                            else:
                                positive += 1
                                
                            if isinstance(steps, list):
                                steps_count += len(steps)
                                steps_items += 1
                            elif isinstance(steps, str):
                                lines = [s for s in steps.splitlines() if s.strip()]
                                steps_count += len(lines)
                                steps_items += 1
                                
                            if isinstance(item.get("description"), str) and "[Pending Confirmation]" in item.get("description"):
                                pending += 1
                                
                    avg_steps = steps_count / steps_items if steps_items else 0.0
                    qm = {
                        "positive": positive,
                        "negative": negative,
                        "edge": edge,
                        "avg_steps": avg_steps,
                        "pending": pending,
                        "generated_count": len(metrics_data) if isinstance(metrics_data, list) else 0
                    }
                    
                    db.add(LogEntry(
                        project_id=project_id,
                        log_type="system",
                        message=f"GEN_QM:{json.dumps(qm, ensure_ascii=False)}",
                        user_id=user_id
                    ))
                    # Also yield to stream for real-time frontend update
                    yield f"GEN_QM:{json.dumps(qm, ensure_ascii=False)}\n"
                    
                    db.commit()
                except Exception as log_e:
                    print(f"Failed to log metrics: {log_e}")

        except Exception as e:
            print(f"Failed to save streamed result to DB: {e}")

    def generate_test_cases_excel(self, requirement: str, project_id: int, db: Session = None, doc_type: str = "requirement", compress: bool = False, user_id: int = None) -> bytes:
        # Generate test cases in JSON format
        json_result = self.generate_test_cases_json(requirement, project_id, db, doc_type, compress, user_id=user_id)
        
        return self.convert_json_to_excel(json_result)

    def convert_json_to_excel(self, json_data: list | dict) -> bytes:
        # Handle dict response (e.g. error or wrapped)
        data = json_data
        if isinstance(json_data, dict):
            if "error" in json_data:
                # Create a single row with error
                data = [{"error": json_data["error"]}]
            # If it's a dict but not error, check if it wraps a list? 
            # The prompt asks for list of objects.
            # If AI returned a dict like {"test_cases": [...]}, we might need to extract.
            # But generate_test_cases_json tries to return list or dict.
            # Let's assume if it's a dict, we wrap it in list, unless it has a known key.
            else:
                 data = [json_data]
                 
        if not isinstance(data, list):
            data = [data] # Fallback

        # Process data to format fields (preconditions, steps)
        processed_data = []
        for item in data:
            if not isinstance(item, dict):
                processed_data.append({"raw": str(item)})
                continue
            
            new_item = item.copy()
            
            # 1. Format preconditions: remove empty strings, join with newlines
            pre = new_item.get("preconditions")
            if isinstance(pre, list):
                pre = [str(p).strip() for p in pre if str(p).strip()]
                new_item["preconditions"] = "\n".join(pre)
            elif isinstance(pre, str):
                 # Try to parse stringified list
                 if pre.strip().startswith("[") and pre.strip().endswith("]"):
                     try:
                         import ast
                         val = ast.literal_eval(pre)
                         if isinstance(val, list):
                             val = [str(p).strip() for p in val if str(p).strip()]
                             new_item["preconditions"] = "\n".join(val)
                     except:
                         pass

            # 2. Format steps: remove empty, add numbering 1. 2.
            # User requirement: remove [''], number sequentially.
            # If sub-content exists, use (1). (2). (Advanced logic omitted for now as AI usually outputs flat list)
            steps = new_item.get("steps")
            if isinstance(steps, list):
                steps = [str(s).strip() for s in steps if str(s).strip()]
                formatted_steps = []
                for i, s in enumerate(steps, 1):
                    formatted_steps.append(f"{i}. {s}")
                new_item["steps"] = "\n".join(formatted_steps)
            elif isinstance(steps, str):
                 if steps.strip().startswith("[") and steps.strip().endswith("]"):
                     try:
                         import ast
                         val = ast.literal_eval(steps)
                         if isinstance(val, list):
                             val = [str(s).strip() for s in val if str(s).strip()]
                             formatted_steps = []
                             for i, s in enumerate(val, 1):
                                 formatted_steps.append(f"{i}. {s}")
                             new_item["steps"] = "\n".join(formatted_steps)
                     except:
                         pass
            
            processed_data.append(new_item)

        # Convert JSON to DataFrame
        df = pd.DataFrame(processed_data)
        
        # Create Excel file in memory with fallback to CSV
        try:
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='Test Cases')
            output.seek(0)
            return output.read()
        except Exception:
            return df.to_csv(index=False).encode('utf-8')

test_generator = TestGenerationModule()
