from core.ai_client import ai_client, get_client_for_user
from sqlalchemy.orm import Session
from core.models import TestGeneration
from modules.knowledge_base import knowledge_base
import json
import pandas as pd
import io
import re
from json import JSONDecoder
import ast

def clean_and_parse_json(response_text: str) -> any:
    cleaned_response = response_text
    result = None
    try:
        # Improved Markdown extraction using regex
        json_block = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', cleaned_response)
        if json_block:
            cleaned_response = json_block.group(1)
        
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
            if root_is_array and isinstance(result, list):
                remaining = cleaned_response[end_idx:].strip()
                while remaining:
                    try:
                        # Skip potential garbage/separators until next '['
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
                last_bracket = cleaned_response.rfind("]")
                if last_bracket != -1:
                    candidate = cleaned_response[: last_bracket + 1]
                    candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
                    try:
                        parsed, _ = decoder.raw_decode(candidate)
                        result = parsed
                    except Exception:
                        # 如果还是失败，可能是中间有错，尝试逐个对象解析
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

class TestGenerationModule:
    def __init__(self):
        pass

    def generate_test_cases_json(self, requirement: str, project_id: int, db: Session = None, doc_type: str = "requirement", compress: bool = False, expected_count: int = 20, batch_size: int = 20, batch_index: int = 0, user_id: int = None) -> dict:
        # Get client for user
        client = get_client_for_user(user_id, db)

        # Retrieve context from Knowledge Base if DB is available
        original_requirement = requirement
        kb_context = ""
        if db:
            if compress:
                # If compression is enabled, we try to summarize the global context
                full_context = knowledge_base.get_all_context(db, project_id)
                kb_context = client.generate_response(full_context, "Compress the following project knowledge into a concise summary capturing entities, constraints, and test-relevant details. Limit to 1500 words. Return plain text.", db=db) if full_context else ""
                
                # Also compress requirement if needed
                try:
                    requirement = client.generate_response(requirement, "Compress the following requirement into a concise version preserving technical details and IDs while removing verbosity. Limit to 1500 words. Return plain text.", db=db)
                except Exception:
                    pass
            else:
                # Use RAG to retrieve relevant context based on requirement
                # Use first 1000 chars of requirement as query to find relevant knowledge
                query_text = requirement[:1000] if requirement else ""
                kb_context = knowledge_base.get_relevant_context(query=query_text, project_id=project_id, limit=5)

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
    
    async def generate_test_cases_stream(self, requirement: str, project_id: int, db: Session = None, doc_type: str = "requirement", compress: bool = False, expected_count: int = 20, batch_size: int = 20, overwrite: bool = False, user_id: int = None):
        # Get client for user
        client = get_client_for_user(user_id, db)

        # Retrieve context from Knowledge Base if DB is available
        original_requirement = requirement
        kb_context = ""
        if db:
            if compress:
                full_context = knowledge_base.get_all_context(db, project_id)
                kb_context = client.generate_response(full_context, "Compress the following project knowledge into a concise summary capturing entities, constraints, and test-relevant details. Limit to 1500 words. Return plain text.", db=db) if full_context else ""
                try:
                    requirement = client.generate_response(requirement, "Compress the following requirement into a concise version preserving technical details and IDs while removing verbosity. Limit to 1500 words. Return plain text.", db=db)
                except Exception:
                    pass
            else:
                query_text = requirement[:1000] if requirement else ""
                kb_context = knowledge_base.get_relevant_context(query=query_text, project_id=project_id, limit=5)

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
        
        full_content = ""
        start_id = 1
        system_prompt = f"""
        {base_prompt}
        
        Reference Knowledge (Use this style/info if relevant):
        {kb_context}
        
        Generate exactly {expected_count} test cases.
        Start the Test Case IDs from {start_id} (e.g., TC-{start_id:03d}).
        Ensure the list length is exactly {expected_count}.
        
        Return ONLY the JSON array.
        """
        
        stream = client.generate_response_stream(requirement, system_prompt)
        
        for chunk in stream:
            full_content += chunk
            yield chunk

        # Post-processing and saving to DB after stream finishes
        try:
            # Try to clean and parse the full content to ensure it's valid JSON before saving
            # Re-use logic from generate_test_cases_json if possible, or just save raw
            # For simplicity, we save the raw string if parsing fails, or parsed structure
            
            # (Simplified parsing logic)
            cleaned_response = json.dumps(clean_and_parse_json(full_content), ensure_ascii=False)
            
            # We don't need to parse perfectly here, just want to save it.
            # But the frontend might expect us to save it so it can load it later.
            
            if db:
                if overwrite:
                    from sqlalchemy import desc
                    query = db.query(TestGeneration).filter(
                        TestGeneration.project_id == project_id,
                        TestGeneration.requirement_text == original_requirement
                    )
                    if user_id:
                        query = query.filter(TestGeneration.user_id == user_id)
                    existing_entry = query.order_by(desc(TestGeneration.created_at)).first()
                    
                    if existing_entry:
                        existing_entry.generated_result = cleaned_response
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
                else:
                    db_entry = TestGeneration(
                        requirement_text=original_requirement,
                        generated_result=cleaned_response, # Save the cleaned text which should be JSON
                        project_id=project_id,
                        user_id=user_id
                    )
                    db.add(db_entry)
                    db.commit()
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
