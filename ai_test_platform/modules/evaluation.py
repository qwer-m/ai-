from core.ai_client import ai_client, get_client_for_user
from sqlalchemy.orm import Session
from core.models import Evaluation, RecallMetric, TestGenerationComparison
import json

class EvaluationModule:
    def calculate_recall(self, retrieved_items: list, relevant_items: list, db: Session = None, project_id: int = None, user_id: int = None) -> float:
        if not relevant_items:
            return 0.0
        
        relevant_set = set(relevant_items)
        retrieved_set = set(retrieved_items)
        
        intersection = relevant_set.intersection(retrieved_set)
        recall = len(intersection) / len(relevant_set)
        
        # Save to DB
        if db:
            try:
                db_entry = RecallMetric(
                    project_id=project_id,
                    retrieved_items=retrieved_items,
                    relevant_items=relevant_items,
                    recall_score=recall,
                    user_id=user_id
                )
                db.add(db_entry)
                db.commit()
            except Exception as e:
                print(f"Failed to save to DB: {e}")
                
        return recall

    def evaluate_test_quality(self, test_case: str, db: Session = None, project_id: int = None, user_id: int = None) -> str:
        client = get_client_for_user(user_id, db)
        system_prompt = """
        You are a Test Quality Auditor.
        Evaluate the quality of the following test case.
        Check for: Clarity, Completeness, correctness of steps vs expected result.
        Give a score out of 10 and a brief explanation.
        """
        result = client.generate_response(test_case, system_prompt)
        
        # Save to DB
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
        - Identify missing cases/steps (Recall loss).
        - Identify hallucinated/unnecessary cases/steps (Precision loss).
        - Identify modified logic (Correction).
        
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
        
        # Clean up result if it contains markdown code blocks
        import re
        match = re.search(r'```json\s*([\s\S]*?)\s*```', result)
        if match:
            result = match.group(1)
        else:
             match = re.search(r'```\s*([\s\S]*?)\s*```', result)
             if match:
                 result = match.group(1)

        # Save to DB
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
                print(f"Failed to save to DB: {e}")
                
        return result

    def evaluate_ui_automation(self, ui_script: str, execution_result: str, db: Session = None, project_id: int = None, user_id: int = None, journey_json: dict = None) -> str:
        client = get_client_for_user(user_id, db)
        
        # New: Calculate Journey Recall
        journey_recall_report = ""
        if journey_json:
            try:
                import ast
                
                # Extract operations from script using AST
                # This is a simplified extraction logic
                operations = []
                try:
                    tree = ast.parse(ui_script)
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                            func = node.value.func
                            # Handle await page.action() or page.action()
                            if isinstance(func, ast.Attribute):
                                action_name = func.attr
                                # Try to capture args for context
                                args_str = ""
                                if node.value.args:
                                    args_str = ", ".join([ast.unparse(arg) for arg in node.value.args])
                                operations.append(f"{action_name}({args_str})")
                except Exception as e:
                    print(f"Failed to parse script with AST: {e}")
                    # Fallback to simple string matching if AST fails (e.g. if script is incomplete)
                    for line in ui_script.split('\n'):
                        if 'page.' in line:
                            operations.append(line.strip())

                # Get journey steps
                journey_steps = []
                if "user_journey" in journey_json:
                    journey_steps = [step.get("action", "") for step in journey_json["user_journey"]]
                
                # Calculate recall (Conceptually)
                # Since exact string match is hard, we use AI to judge coverage
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
        
        # Append the detailed coverage report to the result so the user can see the metrics directly
        if journey_recall_report:
             result += f"\n\n--- Detailed Coverage Report ---\n{journey_recall_report}"
        
        # Save to DB if needed (using existing Evaluation model for simplicity)
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
        
    def evaluate_api_test(self, api_script: str, execution_result: str, db: Session = None, project_id: int = None, user_id: int = None, openapi_spec: str = None) -> str:
        client = get_client_for_user(user_id, db)
        
        # New: API Coverage Analysis
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

        # Append coverage report for visibility
        if api_coverage_report:
            result += f"\n\n--- Detailed API Coverage Report ---\n{api_coverage_report}"
        
        # Save to DB if needed (using existing Evaluation model for simplicity)
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

