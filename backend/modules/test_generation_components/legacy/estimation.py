from sqlalchemy.orm import Session
import re

from .adapters import clean_and_parse_json
from .runtime import LazyAttrProxy, call_component


knowledge_base = LazyAttrProxy("modules.domain.knowledge_base", "knowledge_base")


def get_client_for_user(*args, **kwargs):
    return call_component("core.ai.ai_client", "get_client_for_user", *args, **kwargs)


class LegacyGenerationEstimationMixin:

    def _default_strategy_plan(self) -> dict:
        """
        中文注释：元分析失败时的兜底策略，保证后续链路始终可继续。
        """
        return {
            "system_type": "Unknown",
            "impact_scope": "Regression",
            "complexity": "Medium",
            "suggested_ratios": {
                "functional": 0.6,
                "regression": 0.2,
                "non_functional": 0.2,
            },
            "focus_areas": ["核心流程"],
            "device_scenarios": [],
        }

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
        default_plan = self._default_strategy_plan()
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
                # 中文注释：用默认模板补齐关键字段，避免后续 .get/.join 触发空值错误。
                normalized = {**default_plan, **plan}
                if not isinstance(normalized.get("suggested_ratios"), dict):
                    normalized["suggested_ratios"] = dict(default_plan["suggested_ratios"])
                if not isinstance(normalized.get("focus_areas"), list):
                    normalized["focus_areas"] = list(default_plan["focus_areas"])
                if not isinstance(normalized.get("device_scenarios"), list):
                    normalized["device_scenarios"] = list(default_plan["device_scenarios"])
                return normalized
            print("Meta-analysis fallback: parsed result is not a dict, using default strategy plan")
            return default_plan
        except Exception as e:
            print(f"Meta-analysis failed: {e}")
            # 中文注释：元分析非主链路，不应阻断测试用例生成。
            return default_plan
