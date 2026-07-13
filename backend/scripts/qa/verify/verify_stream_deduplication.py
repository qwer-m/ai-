
import sys
import json
from datetime import datetime
from pathlib import Path

# Add backend root before importing app modules.
BACKEND_ROOT = Path(__file__).resolve().parents[3]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.qa.verify._db_isolation import cleanup_project_test_data, require_explicit_db_write_opt_in

require_explicit_db_write_opt_in("verify_stream_deduplication.py")

from fastapi.testclient import TestClient

from main import app
from core.db.database import SessionLocal
from core.db.models import Project, TestGeneration

client = TestClient(app)

def verify_stream_dedup():
    print("--- Verifying Stream Deduplication ---")
    db = SessionLocal()
    project_id = None
    try:
        # 1. Setup Data
        project_name = "StreamTest_" + datetime.now().strftime("%Y%m%d%H%M%S")
        project = Project(name=project_name)
        db.add(project)
        db.commit()
        db.refresh(project)
        project_id = project.id
        print(f"Created project: {project_id}")

        content = "Stream Requirement Content"
        filename = "stream_req.txt"
        
        # Add through the domain helper so content hashing stays consistent.
        from modules.domain.knowledge_base import knowledge_base
        doc_entry = knowledge_base.add_document(filename, content, "requirement", project_id, db)
        print(f"Added document: {doc_entry}")

        # Add TestGeneration Record
        expected_json = [{"id": 1, "description": "Test Case 1"}]
        gen = TestGeneration(
            project_id=project_id,
            requirement_text=content,
            generated_result=json.dumps(expected_json)
        )
        db.add(gen)
        db.commit()
        db.refresh(gen)
        gen_id = gen.id
        print(f"Created generation record: {gen_id}")

        # 2. Call Stream Endpoint (Force=False)
        # We need to simulate a file upload or text input.
        # The logic checks file upload first.
        
        # Create a dummy file
        files = {'file': (filename, content, 'text/plain')}
        data = {
            'project_id': project_id,
            'doc_type': 'requirement',
            'force': 'false'
        }
        
        print("Calling /api/generate-tests-stream with duplicate file...")
        response = client.post(
            "/api/generate-tests-stream", 
            data=data, 
            files=files,
            headers={"Host": "localhost"}
        )
        
        # Read stream
        stream_content = response.text
        print(f"Stream response length: {len(stream_content)}")
        
        # Check for @@DUPLICATE@@
        if "@@DUPLICATE@@" in stream_content:
            print("Found @@DUPLICATE@@ tag")
            # Extract JSON
            parts = stream_content.split("@@DUPLICATE@@")
            duplicate_part = parts[1].strip()
            print(f"Duplicate part: {duplicate_part}")
            
            # Remove leading : if present (as per my code)
            if duplicate_part.startswith(":"):
                duplicate_part = duplicate_part[1:]
                
            try:
                meta = json.loads(duplicate_part)
                print(f"Parsed metadata: {meta}")
                
                assert meta.get("id") == gen_id, f"Expected ID {gen_id}, got {meta.get('id')}"
                print("ID verification SUCCESS")
                
                # 3. Verify Fetch Endpoint
                print(f"Calling GET /api/test-generations/{meta['id']}...")
                res_get = client.get(f"/api/test-generations/{meta['id']}", headers={"Host": "localhost"})
                print(f"GET Status: {res_get.status_code}")
                if res_get.status_code != 200:
                    print(f"GET Response: {res_get.text}")
                
                assert res_get.status_code == 200
                fetched_data = res_get.json()
                print(f"Fetched data: {fetched_data}")
                assert fetched_data == expected_json
                print("Fetch verification SUCCESS")
                
            except Exception as e:
                print(f"JSON Parse Error: {e}")
                print(f"Content was: {duplicate_part}")
                raise e
        else:
            print("FAILED: Did not find @@DUPLICATE@@ tag")
            print(stream_content)

    except Exception as e:
        print(f"TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if project_id is not None:
            cleanup_project_test_data(db, project_id)
            print(f"Cleaned verification data for project {project_id}")
        db.close()

if __name__ == "__main__":
    verify_stream_dedup()
