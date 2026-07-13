"""
评估模块 (Evaluation Module)

该模块负责对生成的测试用例、UI/API 自动化脚本以及执行结果进行质量评估。
主要功能：
1. 计算需求覆盖率 (Recall Calculation)。
2. 评估测试用例质量 (Test Quality Evaluation)。
3. 评估 UI/API 自动化脚本及执行结果 (Automation Script Evaluation)。
4. 判定测试执行结果 (Test Result Judgment)。
"""

from sqlalchemy.orm import Session
from core.ai.ai_client import get_client_for_user
from core.db.models import Evaluation, TestGenerationComparison
import json
import re


_MISSING_SIGNAL_PATTERNS = (
    re.compile(r"原生成.*未(包含|覆盖|涉及|提供)"),
    re.compile(r"生成.*未(包含|覆盖|涉及|提供)"),
    re.compile(r"(未包含|未覆盖|缺失|缺少|遗漏|漏测|漏掉|需补充|需要补充|补充)"),
    re.compile(r"新增.*(用例|验证|场景|步骤)"),
)

_HALLUCINATION_SIGNAL_PATTERNS = (
    re.compile(r"(多余|冗余|无关|重复|不必要|幻觉|臆造|虚构|凭空|杜撰|不存在|误报|错误断言)"),
    re.compile(r"(生成|原用例).*(多余|冗余|重复|无关)"),
)

def _truncate_utf8_for_mysql_text(value: str, max_bytes: int = 65000) -> str:
    text = value or ""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    clipped = raw[:max_bytes]
    while clipped:
        try:
            return clipped.decode("utf-8")
        except UnicodeDecodeError:
            clipped = clipped[:-1]
    return ""


def _to_clean_text_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            cleaned.append(text)
    return cleaned


def _count_signal(items: list[str], patterns: tuple[re.Pattern[str], ...]) -> int:
    score = 0
    for item in items:
        if any(p.search(item) for p in patterns):
            score += 1
    return score


def _should_swap_defect_lists(missing_points: list[str], hallucinations: list[str]) -> bool:
    """
    当 LLM 明显把 missing_points / hallucinations 方向写反时进行纠偏。
    仅在文本信号足够明显时触发，避免误伤正常结果。
    """
    current_orientation_score = _count_signal(missing_points, _MISSING_SIGNAL_PATTERNS) + _count_signal(hallucinations, _HALLUCINATION_SIGNAL_PATTERNS)
    swapped_orientation_score = _count_signal(missing_points, _HALLUCINATION_SIGNAL_PATTERNS) + _count_signal(hallucinations, _MISSING_SIGNAL_PATTERNS)
    total_signal = current_orientation_score + swapped_orientation_score
    return total_signal >= 2 and swapped_orientation_score >= current_orientation_score + 1


def _normalize_compare_result_json(raw_result: str) -> str:
    text = (raw_result or "").strip()
    if not text:
        return raw_result

    try:
        payload = json.loads(text)
    except Exception:
        return raw_result

    if not isinstance(payload, dict):
        return raw_result

    defect_analysis = payload.get("defect_analysis")
    if not isinstance(defect_analysis, dict):
        return raw_result

    missing_points = _to_clean_text_list(defect_analysis.get("missing_points"))
    hallucinations = _to_clean_text_list(defect_analysis.get("hallucinations"))
    modifications = _to_clean_text_list(defect_analysis.get("modifications"))

    should_swap = _should_swap_defect_lists(missing_points, hallucinations)
    if should_swap:
        missing_points, hallucinations = hallucinations, missing_points

    changed = (
        should_swap
        or defect_analysis.get("missing_points") != missing_points
        or defect_analysis.get("hallucinations") != hallucinations
        or defect_analysis.get("modifications") != modifications
    )
    if not changed:
        return raw_result

    normalized_defect_analysis = dict(defect_analysis)
    normalized_defect_analysis["missing_points"] = missing_points
    normalized_defect_analysis["hallucinations"] = hallucinations
    if "modifications" in defect_analysis or modifications:
        normalized_defect_analysis["modifications"] = modifications

    payload["defect_analysis"] = normalized_defect_analysis
    return json.dumps(payload, ensure_ascii=False, indent=2)

class EvaluationModule:
    """
    评估模块类 (Evaluation Module Class)

    提供多种评估方法，支持基于 LLM 的智能评分和分析。
    """
    def __init__(self):
        pass

    def calculate_recall(self, generated_test: str, requirements: str, db: Session = None, user_id: int = None) -> float:
        """
        计算需求覆盖率 (Calculate Recall)

        使用 LLM 分析生成的测试用例是否覆盖了用户提供的所有需求点。

        Args:
            generated_test: 生成的测试用例内容。
            requirements: 用户原始需求描述。
            db: 数据库会话。
            user_id: 当前用户 ID。

        Returns:
            float: 覆盖率分数 (0.0 - 1.0)。
        """
        def to_points(value) -> set[str]:
            if value is None:
                return set()
            if isinstance(value, list):
                return {str(x).strip().lower() for x in value if str(x).strip()}
            if isinstance(value, str):
                text = value.strip()
                if not text:
                    return set()
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, list):
                        return {str(x).strip().lower() for x in parsed if str(x).strip()}
                except Exception:
                    pass
                parts = re.split(r"[\n,，;；]+", text)
                return {p.strip().lower() for p in parts if p.strip()}
            return {str(value).strip().lower()} if str(value).strip() else set()

        retrieved_points = to_points(generated_test)
        relevant_points = to_points(requirements)

        if not relevant_points:
            return 0.0

        hits = len(retrieved_points & relevant_points)
        recall = hits / len(relevant_points)
        return round(float(recall), 4)

    def evaluate_test_quality(self, test_case: str, db: Session = None, project_id: int = None, user_id: int = None) -> str:
        """
        评估测试用例质量 (Evaluate Test Quality)

        使用 LLM 作为审计员，对测试用例的清晰度、完整性和正确性进行打分。

        Args:
            test_case: 测试用例内容。
            db: 数据库会话。
            project_id: 项目 ID。
            user_id: 用户 ID。

        Returns:
            str: 评估结果文本。
        """
        client = get_client_for_user(user_id, db)
        system_prompt = """
        You are a Test Quality Auditor.
        Evaluate the quality of the following test case.
        Check for: Clarity, Completeness, correctness of steps vs expected result.
        Give a score out of 10 and a brief explanation.
        """
        result = client.generate_response(test_case, system_prompt)

        if db:
            try:
                db_entry = Evaluation(
                    project_id=project_id,
                    test_case_content=test_case,
                    evaluation_result=result,
                    user_id=user_id
                )
                db.add(db_entry)
                db.commit()
            except Exception as e:
                print(f"Failed to save to DB: {e}")

        return result

    def compare_test_cases(self, generated_test_case: str, modified_test_case: str, db: Session = None, project_id: int = None, user_id: int = None) -> str:
        """
        对比测试用例 (Compare Test Cases)

        对比 AI 生成的用例与用户修改后的用例，计算 Precision, Recall, F1 Score 等指标。
        用于分析 AI 的生成质量以及用户的修改意图（缺陷归因分析）。

        Args:
            generated_test_case: AI 生成的原始用例。
            modified_test_case: 用户修改后的最终用例 (Ground Truth)。
            db: 数据库会话。
            project_id: 项目 ID。
            user_id: 用户 ID。

        Returns:
            str: JSON 格式的对比分析结果。
        """
        client = get_client_for_user(user_id, db)
        system_prompt = """
        You are a Test Case Quality Auditor.
        Compare the "Generated Test Case" (AI Output) with the "Modified Test Case" (User's Final Version/Ground Truth).

        Calculate the following metrics based on the content matching:
        1. Precision: Proportion of generated test logic that was kept/used in the modified version.
        2. Recall: Proportion of necessary test logic in the modified version that was originally present in the generated version.
        3. F1 Score: Harmonic mean of Precision and Recall.
        4. Semantic Similarity: Overall semantic similarity score (0.0 to 1.0).

        Perform Defect Attribution Analysis for discrepancies:
        - Identify missing cases/steps (Recall loss): items that EXIST in Modified Test Case but are ABSENT in Generated Test Case.
        - Identify hallucinated/unnecessary cases/steps (Precision loss): items that EXIST in Generated Test Case but are ABSENT in Modified Test Case.
        - Identify modified logic (Correction).

        Field mapping rules (must follow strictly):
        - defect_analysis.missing_points: only list "modified has, generated lacks" items.
        - defect_analysis.hallucinations: only list "generated has, modified lacks" items.
        - If a line implies "新增/补充/原生成未覆盖", it belongs to missing_points, NOT hallucinations.

        Return the result strictly in the following JSON format:
        {
            "metrics": {
                "precision": 0.0,
                "recall": 0.0,
                "f1_score": 0.0,
                "semantic_similarity": 0.0
            },
            "defect_analysis": {
                "missing_points": ["point 1", "point 2"],
                "hallucinations": ["point 1", "point 2"],
                "modifications": ["point 1", "point 2"]
            },
            "summary": "Brief text summary of the comparison."
        }

        LANGUAGE CONSTRAINT:
        All natural language content in the output (including "summary" and lists in "defect_analysis") MUST be in Chinese (Simplified).
        """
        prompt = f"Generated Test Case:\n{generated_test_case}\n\nModified Test Case:\n{modified_test_case}"
        result = client.generate_response(prompt, system_prompt)

        import re
        match = re.search(r'```json\s*([\s\S]*?)\s*```', result)
        if match:
            result = match.group(1)
        else:
             match = re.search(r'```\s*([\s\S]*?)\s*```', result)
             if match:
                 result = match.group(1)

        result = _normalize_compare_result_json(result)

        if db:
            try:
                db_entry = TestGenerationComparison(
                    project_id=project_id,
                    generated_test_case=generated_test_case,
                    modified_test_case=modified_test_case,
                    comparison_result=result,
                    user_id=user_id
                )
                db.add(db_entry)
                db.commit()
            except Exception as e:
                db.rollback()
                err = str(e)
                if "Data too long" in err:
                    try:
                        db_entry = TestGenerationComparison(
                            project_id=project_id,
                            generated_test_case=_truncate_utf8_for_mysql_text(generated_test_case),
                            modified_test_case=_truncate_utf8_for_mysql_text(modified_test_case),
                            comparison_result=_truncate_utf8_for_mysql_text(result),
                            user_id=user_id,
                        )
                        db.add(db_entry)
                        db.commit()
                    except Exception as e2:
                        db.rollback()
                        print(f"Failed to save comparison to DB after truncate: {e2}")
                else:
                    print(f"Failed to save comparison to DB: {e}")

        return result

    def evaluate_ui_automation(self, ui_script: str, execution_result: str, db: Session = None, project_id: int = None, user_id: int = None, journey_json: dict = None) -> str:
        client = get_client_for_user(user_id, db)

        journey_recall_report = ""
        if journey_json:
            try:
                import ast

                operations = []
                try:
                    tree = ast.parse(ui_script)
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                            func = node.value.func
                            if isinstance(func, ast.Attribute):
                                action_name = func.attr
                                args_str = ""
                                if node.value.args:
                                    args_str = ", ".join([ast.unparse(arg) for arg in node.value.args])
                                operations.append(f"{action_name}({args_str})")
                except Exception as e:
                    print(f"Failed to parse script with AST: {e}")
                    for line in ui_script.split('\n'):
                        if 'page.' in line:
                            operations.append(line.strip())

                journey_steps = []
                if "user_journey" in journey_json:
                    journey_steps = [step.get("action", "") for step in journey_json["user_journey"]]

                recall_prompt = f"""
                You are a UI Automation Coverage Analyst.

                Task: Calculate the coverage of the User Journey by the provided Automation Script Operations.

                User Journey Steps:
                {json.dumps(journey_steps, indent=2, ensure_ascii=False)}

                Extracted Script Operations:
                {json.dumps(operations, indent=2, ensure_ascii=False)}

                Please determine which User Journey Steps are covered by the Script Operations.
                A step is covered if there is a corresponding operation sequence in the script.

                Return a JSON object with:
                - covered_steps: list of covered step descriptions
                - missing_steps: list of missing step descriptions
                - coverage_rate: float (0.0 to 1.0)
                - explanation: brief explanation
                """

                coverage_analysis = client.generate_response(recall_prompt, "You are a JSON generator. Output only valid JSON.", db=db)
                journey_recall_report = f"\n\nJourney Coverage Analysis:\n{coverage_analysis}"

            except Exception as e:
                journey_recall_report = f"\n\nJourney Coverage Analysis Failed: {str(e)}"

        system_prompt = """
        You are a UI Automation Test Evaluator.
        Evaluate the quality and effectiveness of the following UI automation script and its execution result.

        Evaluation criteria:
        1. Script Structure: Is the script well-structured with proper setup and teardown?
        2. Error Handling: Does the script handle potential errors gracefully?
        3. Test Coverage: Does the script effectively cover the intended UI functionality?
        4. Execution Success: Did the script execute successfully?
        5. Result Reporting: Does the script provide clear test results?

        Give a comprehensive evaluation with scores out of 10 for each criterion and an overall score.
        """
        prompt = f"UI Automation Script:\n{ui_script}\n\nExecution Result:\n{execution_result}{journey_recall_report}"
        result = client.generate_response(prompt, system_prompt, db=db)

        if journey_recall_report:
             result += f"\n\n--- Detailed Coverage Report ---\n{journey_recall_report}"

        if db:
            try:
                db_entry = Evaluation(
                    project_id=project_id,
                    test_case_content=f"UI Automation: {ui_script[:100]}...",
                    evaluation_result=result,
                    user_id=user_id
                )
                db.add(db_entry)
                db.commit()
            except Exception as e:
                print(f"Failed to save to DB: {e}")

        return result

    def judge_test_result(self, input_data: dict, actual_output: dict, expected_behavior: str, db: Session = None, user_id: int = None) -> dict:
        client = get_client_for_user(user_id, db)

        system_prompt = """
        You are an AI Test Result Judge.
        Analyze the Input, Actual Output, and Expected Behavior.
        Classify the result into ONE of these categories:
        - Normal: Result matches expectation (Pass).
        - Abnormal: System error, 500, or crash (Fail).
        - False Positive: Test failed (e.g. 400 Bad Request) but it was EXPECTED due to invalid input (Business Pass).
        - False Negative: Test passed (200 OK) but data is wrong (Business Fail).

        Return JSON:
        {
            "category": "Normal" | "Abnormal" | "False Positive" | "False Negative",
            "reason": "explanation"
        }
        """

        prompt = f"""
        Input: {json.dumps(input_data)}
        Actual Output: {json.dumps(actual_output)}
        Expected Behavior: {expected_behavior}
        """

        response = client.generate_response(prompt, system_prompt, db=db)
        try:
            from core.processing.utils import extract_code_block
            return json.loads(extract_code_block(response, "json"))
        except:
            return {"category": "Unknown", "reason": "Failed to parse AI response"}

    def evaluate_api_test(self, api_script: str, execution_result: str, db: Session = None, project_id: int = None, user_id: int = None, openapi_spec: str = None) -> str:
        """
        评估 API 测试脚本 (Evaluate API Test)

        评估 API 测试脚本的代码质量、断言完整性以及执行结果分析。

        Args:
            api_script: 生成的 API 测试脚本。
            execution_result: 执行结果。
            db: 数据库会话。
            project_id: 项目 ID。
            user_id: 用户 ID。

        Returns:
            str: 评估报告。
        """
        client = get_client_for_user(user_id, db)

        api_coverage_report = ""
        if openapi_spec:
            coverage_prompt = f"""
            You are an API Test Coverage Analyst.

            Task: Compare the API Test Script against the OpenAPI Specification to determine endpoint coverage.

            OpenAPI Spec (Snippet/Summary):
            {openapi_spec[:2000]}... (truncated if too long)

            API Test Script:
            {api_script}

            Return a JSON object with:
            - covered_endpoints: list of endpoints (method + path) called in the script
            - missing_endpoints: list of key endpoints from spec not covered
            - coverage_rate: float (0.0 to 1.0) estimation
            """

            coverage_analysis = client.generate_response(coverage_prompt, "You are a JSON generator. Output only valid JSON.", db=db)
            api_coverage_report = f"\n\nAPI Coverage Analysis:\n{coverage_analysis}"

        system_prompt = """
        You are an API Test Evaluator.
        Evaluate the quality and effectiveness of the following API test script and its execution result.

        Evaluation criteria:
        1. Script Structure: Is the script well-structured with proper organization?
        2. Assertions: Does the script include appropriate assertions to verify API responses?
        3. Error Handling: Does the script handle potential API errors gracefully?
        4. Test Coverage: Does the script effectively test the intended API functionality?
        5. Execution Success: Did the script execute successfully?

        Give a comprehensive evaluation with scores out of 10 for each criterion and an overall score.
        """
        prompt = f"API Test Script:\n{api_script}\n\nExecution Result:\n{execution_result}{api_coverage_report}"
        result = client.generate_response(prompt, system_prompt, db=db)

        if api_coverage_report:
            result += f"\n\n--- Detailed API Coverage Report ---\n{api_coverage_report}"

        if db:
            try:
                db_entry = Evaluation(
                    project_id=project_id,
                    test_case_content=f"API Test: {api_script[:100]}...",
                    evaluation_result=result,
                    user_id=user_id
                )
                db.add(db_entry)
                db.commit()
            except Exception as e:
                print(f"Failed to save to DB: {e}")

        return result

evaluator = EvaluationModule()
