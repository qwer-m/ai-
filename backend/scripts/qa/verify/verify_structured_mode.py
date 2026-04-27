import requests
import json
import time

BASE_URL = "http://localhost:8000"

def verify_structured_api():
    print("--- Verifying Structured API Generation ---")
    
    # 1. Create a Project (if needed, but we might just pick one)
    # Let's assume project_id=1 exists or create one.
    # Actually, let's just use project_id=1 for testing if it exists, or handle error.
    
    # Payload for structured mode
    structured_req = [
      {
        "method": "POST",
        "path": "/users",
        "description": "Create a new user",
        "params": {
          "username": "string (required, 3-20 chars)",
          "age": "integer (0-120)",
          "email": "string (email format)"
        }
      }
    ]
    
    payload = {
        "requirement": json.dumps(structured_req),
        "project_id": 1,
        "base_url": "http://localhost:8000",
        "test_types": ["Functional", "Boundary"],
        "mode": "structured"
    }
    
    print("Sending POST /api/api-testing with structured mode...")
    try:
        start_time = time.time()
        resp = requests.post(f"{BASE_URL}/api/api-testing", json=payload)
        duration = time.time() - start_time
        
        print(f"Status Code: {resp.status_code}")
        print(f"Duration: {duration:.2f}s")
        
        if resp.status_code == 200:
            data = resp.json()
            script = data.get("script", "")
            print(f"Script Length: {len(script)}")
            
            # Basic checks on generated script
            if "def test_" in script and "requests.post" in script:
                print("SUCCESS: Generated script contains test functions.")
            else:
                print("WARNING: Script might be malformed.")
                print(script[:500])
                
            # Check structured report if execution happened
            if "structured_report" in data and data["structured_report"]:
                report = data["structured_report"]
                print(f"Execution Report: Total={report.get('total')}, Failed={report.get('failed')}")
            else:
                print("No execution report (maybe execution failed or mocked).")
        else:
            print(f"FAILED: {resp.text}")
            
    except Exception as e:
        print(f"EXCEPTION: {e}")

if __name__ == "__main__":
    verify_structured_api()
