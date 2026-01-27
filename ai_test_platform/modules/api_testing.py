from core.ai_client import ai_client, get_client_for_user
from sqlalchemy.orm import Session
from core.models import APIExecution
from core.utils import extract_code_block, run_temp_script
from core.prompt_loader import prompt_loader
import subprocess
import os
import re
import json
import xml.etree.ElementTree as ET
import tempfile
import ast

# Error Constants
ERROR_TYPE_GENERATION = "AI_GENERATION_ERROR"
ERROR_TYPE_COMPILATION = "COMPILATION_ERROR"
ERROR_TYPE_EXECUTION = "EXECUTION_ERROR"

class APITestingModule:
    """
    API 自动化测试模块 (API Testing Module)
    
    此模块负责生成、执行和管理 API 自动化测试。
    
    核心功能：
    1. 脚本生成：利用 LLM 根据需求或接口文档生成 pytest 测试脚本。
    2. 脚本执行：在沙箱环境中执行生成的脚本，并捕获输出。
    3. 报告解析：解析 JUnit XML 格式的测试报告，生成结构化结果。
    4. 场景生成：支持生成包含多个接口调用的链式测试场景 (Chain Script)。
    5. 数据构造：生成用于 Fuzzing (模糊测试) 的 Mock 数据。
    
    依赖：
    - core.ai_client: 调用 LLM 生成代码。
    - pytest: 底层测试执行框架。
    """
    def generate_api_test_script(self, requirement: str, base_url: str = "", api_path: str = "", test_types: list[str] = None, api_docs: str = "", db: Session = None, mode: str = "natural", user_id: int = None) -> str:
        """
        生成 API 测试脚本 (Generate API Test Script)
        
        根据用户需求、BaseURL、API 路径和文档，生成可执行的 pytest 脚本。
        支持两种模式：
        - natural: 自然语言模式，生成的脚本更灵活。
        - structured: 结构化模式，遵循特定的模板。
        
        注入指令：
        强制脚本打印详细的 Request/Response 信息 (Headers, Body) 到标准输出，
        以便前端或日志系统捕获并展示调试信息。
        """
        client = get_client_for_user(user_id, db)
        test_types_str = ", ".join(test_types) if test_types else "Functional"
        
        # Select prompt template based on mode
        prompt_name = "api_test_generator_structured" if mode == "structured" else "api_test_generator"
        
        # Prepare Requirement String (Append api_path if provided)
        final_requirement = requirement
        if api_path:
            final_requirement = f"Target Endpoint: {api_path}\n{requirement}"

        # Add instruction to print response details for frontend capture
        final_requirement += """
        
        IMPORTANT INSTRUCTION FOR SCRIPT GENERATION:
        The generated script MUST print the actual HTTP response headers and body to stdout for the execution runner to capture them.
        Use the following format exactly:
        
        print("<<<HEADERS_START>>>")
        print(json.dumps(dict(response.headers)))
        print("<<<HEADERS_END>>>")
        print("<<<BODY_START>>>")
        print(response.text)
        print("<<<BODY_END>>>")
        
        Ensure this printing happens after the request is made.
        """

        # Load and render system prompt from YAML
        system_prompt = prompt_loader.get_rendered_prompt(
            prompt_name,
            base_url=base_url if base_url else "http://localhost", # Default or placeholder
            output_path="report.xml",
            test_requirements=f"{final_requirement} (Focus: {test_types_str})"
        )
        
        # If loader fails, fallback to a minimal prompt (Safety net)
        if not system_prompt:
             system_prompt = f"Generate a pytest script for {base_url} testing {requirement}."

        prompt = f"Requirement: {requirement}\nAPI Context: {api_docs}"
        response = client.generate_response(prompt, system_prompt, db=db)
        
        return extract_code_block(response, "python")

    def generate_mock_data(self, interface_info: dict, mock_type: str = "single", count: int = 5, db: Session = None, user_id: int = None) -> list:
        """
        生成 Mock/Fuzzing 数据 (Generate Mock Data)
        
        针对特定接口，生成多样化的测试数据，用于健壮性测试。
        覆盖场景：Happy Path, 边界值, 类型错误, 特殊字符注入, 空值等。
        """
        client = get_client_for_user(user_id, db)
        
        system_prompt = """
        You are an API Testing Expert specializing in Fuzzing and Mock Data Generation.
        Your task is to generate a list of test cases with varied input data to test the robustness of an API.
        
        Generate diverse test cases including:
        1. Happy Path (Valid data)
        2. Boundary Values (Min/Max/Off-by-one)
        3. Invalid Data Types (String for Int, etc.)
        4. Special Characters / Injection Payloads (SQLi, XSS strings)
        5. Empty/Null Values
        
        Return ONLY a JSON array of test cases. Each test case should have:
        - name: Description of the test case
        - params: Key-value pairs for query parameters
        - headers: Key-value pairs for headers
        - body: JSON body or string content
        - expected_status: Expected HTTP status code (e.g., 200, 400)
        """
        
        prompt = f"""
        Target Interface:
        Method: {interface_info.get('method')}
        URL: {interface_info.get('url')}
        Base Params: {interface_info.get('params')}
        Base Body: {interface_info.get('body')}
        
        Generate {count} fuzzing test cases.
        """
        
        response = client.generate_response(prompt, system_prompt, db=db)
        try:
            return json.loads(extract_code_block(response, "json"))
        except:
            return []

    def generate_chain_script(self, interfaces: list[dict], scenario_desc: str, db: Session = None, user_id: int = None) -> str:
        """
        生成链式场景脚本 (Generate Chain Script)
        
        生成包含多个 API 调用的复杂场景脚本。
        核心逻辑：
        1. 依赖处理：自动提取上一步的响应数据（如 Token, ID）作为下一步的输入。
        2. 状态断言：每一步都验证成功状态。
        3. 数据传递：使用变量在步骤间传递数据。
        """
        client = get_client_for_user(user_id, db)
        
        system_prompt = """
        You are an API Automation Expert.
        Generate a Python script (using pytest and requests) that executes a CHAIN of API requests.
        
        Requirements:
        1. Dependency Handling: Extract data from response N and pass to request N+1 (e.g., token, ID).
        2. Assertions: Verify each step success before proceeding.
        3. Data Passing: Use variables to pass data between steps.
        4. Return ONLY the python code.
        """
        
        interfaces_str = json.dumps(interfaces, indent=2)
        prompt = f"""
        Scenario: {scenario_desc}
        
        Interfaces Sequence:
        {interfaces_str}
        
        Generate a chained test script.
        """
        
        response = client.generate_response(prompt, system_prompt, db=db)
        return extract_code_block(response, "python")

    def parse_junit_report(self, report_path: str = None, error_type: str = None, error_message: str = None) -> dict:
        """
        解析 JUnit 测试报告 (Parse JUnit Report)
        
        解析 pytest 生成的 JUnit XML 报告，转化为前端友好的 JSON 结构。
        如果发生系统级错误（如编译失败、崩溃），则生成伪造的失败报告。
        """
        if error_type:
            # Construct a structured failure report for system errors
            return {
                "total": 1,
                "passed": 0,
                "failed": 1,
                "skipped": 0,
                "time": 0.0,
                "failures": [{
                    "name": "System Check",
                    "message": error_type,
                    "details": error_message,
                    "type": "error"
                }]
            }

        try:
            if not report_path or not os.path.exists(report_path):
                 return self.parse_junit_report(error_type=ERROR_TYPE_EXECUTION, error_message="Test report was not generated. This usually means pytest failed to start or crashed.")

            tree = ET.parse(report_path)
            root = tree.getroot()
            
            total = 0
            passed = 0
            failed = 0
            error = 0
            skipped = 0
            time = 0.0
            failures = []
            
            def process_suite(suite):
                nonlocal total, passed, failed, error, skipped, time
                total += int(suite.attrib.get('tests', 0))
                failed += int(suite.attrib.get('failures', 0))
                error += int(suite.attrib.get('errors', 0))
                skipped += int(suite.attrib.get('skipped', 0))
                time += float(suite.attrib.get('time', 0))
                
                for case in suite.findall('testcase'):
                    failure = case.find('failure')
                    error_elem = case.find('error')
                    if failure is not None:
                        failures.append({
                            "name": case.attrib.get('name'),
                            "message": failure.attrib.get('message'),
                            "details": failure.text
                        })
                    elif error_elem is not None:
                        failures.append({
                            "name": case.attrib.get('name'),
                            "message": error_elem.attrib.get('message'),
                            "details": error_elem.text,
                            "type": "error"
                        })

            if root.tag == 'testsuites':
                for suite in root.findall('testsuite'):
                    process_suite(suite)
            else:
                process_suite(root)
                
            passed = total - failed - error - skipped
            return {
                "total": total,
                "passed": passed,
                "failed": failed + error,
                "skipped": skipped,
                "time": time,
                "failures": failures
            }
        except Exception as e:
            return self.parse_junit_report(error_type=ERROR_TYPE_EXECUTION, error_message=f"Failed to parse report: {str(e)}")

    def execute_api_tests(self, script_content: str, requirement: str = "", base_url: str = "", db: Session = None, project_id: int = None, user_id: int = None) -> dict:
        """
        执行 API 测试脚本 (Execute API Tests)
        
        流程：
        1. 验证 BaseURL 格式。
        2. 语法检查 (AST Parse) 确保脚本可运行。
        3. 创建临时文件并使用 pytest 运行。
        4. 解析执行结果和 JUnit XML 报告。
        5. 将执行记录保存到数据库 (APIExecution)。
        """
        # 1. Validate URL
        if base_url and not re.match(r'^https?://', base_url):
             return {"result": "Invalid Base URL", "structured_report": self.parse_junit_report(error_type="VALIDATION_ERROR", error_message="Base URL must start with http:// or https://")}
        
        # 2. Syntax Check (Compilation Error)
        try:
            ast.parse(script_content)
        except SyntaxError as e:
            error_msg = f"Syntax Error at line {e.lineno}: {e.msg}\n{e.text}"
            return {
                "result": error_msg,
                "structured_report": self.parse_junit_report(error_type=ERROR_TYPE_COMPILATION, error_message=error_msg)
            }
        except Exception as e:
            return {
                "result": str(e),
                "structured_report": self.parse_junit_report(error_type=ERROR_TYPE_COMPILATION, error_message=str(e))
            }

        # 3. Execution
        report_path = ""
        with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
            report_path = tmp.name
        
        try:
            # Execute with pytest
            stdout, stderr, return_code = run_temp_script(script_content, command=["pytest", f"--junitxml={report_path}"], timeout=30)
            
            output_result = f"Pytest Output:\n{stdout}"
            if stderr:
                output_result += f"\nStderr:\n{stderr}"

            # 4. Report Parsing
            structured_report = None
            
            # If pytest crashed (return code != 0 and no report), treat as execution error
            if not os.path.exists(report_path) or os.path.getsize(report_path) == 0:
                 structured_report = self.parse_junit_report(error_type=ERROR_TYPE_EXECUTION, error_message=output_result)
            else:
                 structured_report = self.parse_junit_report(report_path)
                 # If XML was generated but contains no tests and we have errors (e.g. collection failure), treat as execution error
                 if structured_report and structured_report['total'] == 0 and stderr:
                      structured_report = self.parse_junit_report(error_type=ERROR_TYPE_EXECUTION, error_message=output_result)

            # Save to DB
            if db:
                try:
                    db_entry = APIExecution(
                        project_id=project_id,
                        requirement=requirement,
                        generated_script=script_content,
                        execution_result=output_result,
                        structured_report=structured_report,
                        user_id=user_id
                    )
                    db.add(db_entry)
                    db.commit()
                except Exception as e:
                    print(f"Failed to save to DB: {e}")
            
            return {
                "result": output_result,
                "structured_report": structured_report
            }
        finally:
            if os.path.exists(report_path):
                try:
                    os.remove(report_path)
                except:
                    pass

api_tester = APITestingModule()
