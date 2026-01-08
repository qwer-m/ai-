from core.ai_client import ai_client, get_client_for_user
from sqlalchemy.orm import Session
from core.models import APIExecution
from core.utils import extract_code_block, run_temp_script
from core.prompt_loader import prompt_loader
import subprocess
import os
import re
import xml.etree.ElementTree as ET
import tempfile
import ast

# Error Constants
ERROR_TYPE_GENERATION = "AI_GENERATION_ERROR"
ERROR_TYPE_COMPILATION = "COMPILATION_ERROR"
ERROR_TYPE_EXECUTION = "EXECUTION_ERROR"

class APITestingModule:
    def generate_api_test_script(self, requirement: str, base_url: str = "", api_path: str = "", test_types: list[str] = None, api_docs: str = "", db: Session = None, mode: str = "natural", user_id: int = None) -> str:
        client = get_client_for_user(user_id, db)
        test_types_str = ", ".join(test_types) if test_types else "Functional"
        
        # Select prompt template based on mode
        prompt_name = "api_test_generator_structured" if mode == "structured" else "api_test_generator"
        
        # Prepare Requirement String (Append api_path if provided)
        final_requirement = requirement
        if api_path:
            final_requirement = f"Target Endpoint: {api_path}\n{requirement}"

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

    def parse_junit_report(self, report_path: str = None, error_type: str = None, error_message: str = None) -> dict:
        """
        Parses JUnit XML report or generates a fake report for system errors.
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
