
import sys
import os
import json
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from datetime import datetime

# Add path
sys.path.append(os.path.join(os.getcwd(), "ai_test_platform"))

from main import app
from core.database import SessionLocal, engine
from core.models import Project, TestGeneration, KnowledgeDocument

client = TestClient(app)

def verify_stream_dedup():
    print("--- Verifying Stream Deduplication ---")
    db = SessionLocal()
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
        
        # Add Knowledge Document
        doc = KnowledgeDocument(
            project_id=project_id,
            filename=filename,
            content=content,
            doc_type="requirement",
            content_hash="hash_placeholder" # logic computes hash, but we insert manually for speed or use API?
            # Better to use API to ensure hash is computed correctly if we rely on it.
            # But here we rely on requirement_text match in TestGeneration.
        )
        # Actually, let's use the helper to add document to ensure consistent hashing
        from modules.knowledge_base import knowledge_base
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
        db.close()

if __name__ == "__main__":
    verify_stream_dedup()
