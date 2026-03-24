from sqlalchemy.orm import Session
import re

from core.ai_client import get_client_for_user
from modules.knowledge_base import knowledge_base
from modules.test_generation_components.legacy.adapters import clean_and_parse_json


class LegacyGenerationEstimationMixin:

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
            7. **Assume Atomic Test Cases (The 5 Pillars)**: 
               - Each case covers exactly ONE checkable point (Zero Coupling).
               - Do not count "End-to-End" flows as single cases if they cover multiple distinct features.
               - Do not count redundant "Water Injection" cases (e.g. 10 valid inputs for same field).
               - Enforce MECE (Mutually Exclusive, Collectively Exhaustive).
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
            print(f"Estimation failed ({type(e).__name__}): {e}")
            raise e  # Propagate error to let frontend handle it, no fallback guessing

    def analyze_requirement_context(self, requirement: str, kb_context: str, client, db: Session) -> dict:
        """
        Meta-Analysis Agent: Analyzes the requirement and knowledge base to determine test strategy.
        Returns a dictionary with system_type, impact_scope, test_ratios, and focus_areas.
        """
        try:
            analysis_prompt = f"""
            You are a QA Architect. Analyze the following Requirement and Reference Context.
            Determine the System Type, Impact Scope, and optimal Testing Strategy.
            
            Requirement Preview: {requirement[:1000]}...
            Reference Context Preview: {kb_context[:1000]}...
            
            ANALYSIS GUIDELINES (System Type Detection):
            - **Web**: Keywords like "Browser", "URL", "Page", "H5", "网页", "后台".
            - **Mobile App**: Keywords like "iOS", "Android", "APK", "Touch", "Swipe", "手机", "APP".
            - **Tablet/Pad**: Keywords like "iPad", "Tablet", "Landscape", "Split Screen", "平板", "HD".
            - **Desktop App**: Keywords like "Windows", "Mac", "Client", "Exe", "Install", "PC客户端", "电脑版".
            - **Combination**: If multiple platforms are detected, combine them (e.g., "Mobile + Web", "Tablet + Desktop + Web").

            TEST CASE DESIGN PRINCIPLES (Strategy Level):
            1. **Comprehensive Coverage**: Ensure all functional and non-functional aspects are covered.
            2. **Clear Purpose**: Each test area must have a specific, identifiable goal.
            3. **Minimal Workload (MECE)**: Avoid redundancy. Strategy must be efficient.
            4. **Clear Classification**: Organize by modules logically.
            5. **Independence (Zero Coupling)**: Plan for atomic test points. Avoid overlapping scope between areas.

            Output STRICT JSON:
            {{
                "system_type": "String describing the system type (e.g., 'Web', 'Mobile App', 'Tablet + Web', 'Mobile + Web + Desktop')",
                "impact_scope": "New Feature" | "Regression" | "Hotfix" | "Refactor",
                "complexity": "High" | "Medium" | "Low",
                "suggested_ratios": {{
                    "functional": 0.6,
                    "regression": 0.2,
                    "non_functional": 0.2
                }},
                "focus_areas": ["Login", "Payment", "API", "UI", "Security", "Performance", "Responsiveness", "Cross-Platform"],
                "device_scenarios": ["Weak Network", "Landscape", "Battery Drain", "Browser Compatibility", "Mouse/Keyboard", "Touch"]
            }}
            """
            response = client.generate_response(requirement[:2000], analysis_prompt, db=db) # Use limited req for speed
            plan = clean_and_parse_json(response)
            if isinstance(plan, dict):
                return plan
        except Exception as e:
            print(f"Meta-analysis failed: {e}")
            # Re-raise the exception to let the user know something went wrong
            # instead of silently downgrading to a default plan.
            raise e
