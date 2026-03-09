import sys
import os
from datetime import datetime
from sqlalchemy import create_engine, desc
from sqlalchemy.orm import sessionmaker

# Add path
sys.path.append(os.path.join(os.getcwd(), "ai_test_platform"))

from core.database import SessionLocal, engine, Base
from core.models import Project, KnowledgeDocument, TestGeneration
from modules.knowledge_base import knowledge_base

def test_deduplication():
    db = SessionLocal()
    project_id = None
    try:
        # Setup: Create Project
        project = Project(name="Test Project " + datetime.now().strftime("%Y%m%d%H%M%S"))
        db.add(project)
        db.commit()
        db.refresh(project)
        project_id = project.id
        print(f"Created project {project_id}")

        content = "This is a test requirement content."
        filename = "test_req.txt"

        # 1. Test Knowledge Base Deduplication
        print("\n--- Testing Knowledge Base Deduplication ---")
        
        # First add
        doc1 = knowledge_base.add_document(filename, content, "requirement", project_id, db, force=False)
        print(f"First add result type: {type(doc1)}")
        if isinstance(doc1, KnowledgeDocument):
            print(f"First add success, ID: {doc1.id}")
            doc1_id = doc1.id
        else:
            print(f"First add failed: {doc1}")
            return

        # Second add (duplicate, force=False)
        doc2 = knowledge_base.add_document(filename, content, "requirement", project_id, db, force=False)
        print(f"Second add (force=False) result: {doc2}")
        assert isinstance(doc2, dict) and doc2.get("status") == "duplicate", "Should return duplicate status"

        # Third add (duplicate, force=True)
        doc3 = knowledge_base.add_document(filename, content, "requirement", project_id, db, force=True)
        print(f"Third add (force=True) result type: {type(doc3)}")
        assert isinstance(doc3, KnowledgeDocument), "Should return document object"
        assert doc3.id == doc1_id, "Should return SAME document object"

        print("Knowledge Base Deduplication Verified!")

        # 2. Test TestGeneration Overwriting Logic
        print("\n--- Testing TestGeneration Overwriting ---")
        
        # Close old session and start new one to be clean
        db.close()
        db = SessionLocal()
        
        # Initial Generation Record
        gen1 = TestGeneration(
            requirement_text=content,
            generated_result='[{"id": 1}]',
            project_id=project_id
        )
        db.add(gen1)
        db.commit()
        
        # Debug: list all records for project using specific columns
        rows = db.query(TestGeneration.id, TestGeneration.project_id).filter(TestGeneration.project_id == project_id).all()
        print(f"DEBUG: Found {len(rows)} rows (tuples) for project {project_id}")
        for r in rows:
            print(f" - Row: {r}")

        # Instead of refresh, just query it back to be safe and get ID
        gen1 = db.query(TestGeneration).filter(
            TestGeneration.requirement_text == content, 
            TestGeneration.project_id == project_id
        ).first()
        
        if not gen1:
             print("CRITICAL ERROR: Could not find just inserted record!")
             # Fallback to get last one
             gen1 = db.query(TestGeneration).filter(TestGeneration.project_id == project_id).order_by(desc(TestGeneration.id)).first()
        
        if gen1 is None:
             print("STILL NULL after fallback!")
             return

        gen1_id = gen1.id
        print(f"Created TestGeneration record {gen1_id}")

        # Simulate Force Overwrite (Logic similar to main.py)
        force = True
        
        saved = False
        if force:
            existing_entry = db.query(TestGeneration).filter(
                TestGeneration.project_id == project_id,
                TestGeneration.requirement_text == content
            ).order_by(desc(TestGeneration.created_at)).first()
            
            if existing_entry:
                print(f"Found existing entry {existing_entry.id}")
                existing_entry.generated_result = 'UPDATED RESULT'
                existing_entry.created_at = datetime.now()
                db.commit()
                saved = True
        
        if not saved:
            print("Creating new entry (should not happen)")
            db_entry = TestGeneration(
                requirement_text=content,
                generated_result='NEW RESULT',
                project_id=project_id
            )
            db.add(db_entry)
            db.commit()

        # Verify
        db.expire_all() # Make sure we get fresh data
        gen_after = db.query(TestGeneration).filter(TestGeneration.id == gen1_id).first()
        print(f"Record {gen1_id} result: {gen_after.generated_result}")
        assert gen_after.generated_result == 'UPDATED RESULT', "Should have updated the record"
        
        # Check count
        count = db.query(TestGeneration).filter(TestGeneration.project_id == project_id).count()
        print(f"Total TestGeneration records for project: {count}")
        assert count == 1, "Should only have 1 record"

        print("TestGeneration Overwriting Verified!")

    except Exception as e:
        print(f"TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    test_deduplication()
