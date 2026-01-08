from core.ai_client import ai_client, get_client_for_user
from sqlalchemy.orm import Session
from core.models import Evaluation, RecallMetric, TestGenerationComparison

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
        You are a Test Case Comparison Expert.
        Compare the following two test cases and identify functional differences.
        Focus on: Missing test cases, Added test cases, Modified test steps, Changed expected results, Priority changes.
        Provide a detailed comparison report.
        """
        prompt = f"Generated Test Case:\n{generated_test_case}\n\nModified Test Case:\n{modified_test_case}"
        result = client.generate_response(prompt, system_prompt)
        
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

    def evaluate_ui_automation(self, ui_script: str, execution_result: str, db: Session = None, project_id: int = None, user_id: int = None) -> str:
        client = get_client_for_user(user_id, db)
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
        prompt = f"UI Automation Script:\n{ui_script}\n\nExecution Result:\n{execution_result}"
        result = client.generate_response(prompt, system_prompt)
        
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
        
    def evaluate_api_test(self, api_script: str, execution_result: str, db: Session = None, project_id: int = None, user_id: int = None) -> str:
        client = get_client_for_user(user_id, db)
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
        prompt = f"API Test Script:\n{api_script}\n\nExecution Result:\n{execution_result}"
        result = client.generate_response(prompt, system_prompt, db=db)
        
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

