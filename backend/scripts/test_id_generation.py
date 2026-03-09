import sys
import os

# Add the project directory to the path
sys.path.append(os.path.join(os.path.dirname(__file__), 'ai_test_platform'))

from modules.knowledge_base import KnowledgeBaseModule
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from core.models import Base

# Create engine and session for test database
db_url = "sqlite:///test_id_generation.db"
engine = create_engine(db_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create tables if they don't exist
Base.metadata.create_all(bind=engine)

# Create knowledge base instance
kb = KnowledgeBaseModule()

# Test ID generation
def test_incomplete_doc_id_generation():
    print("Testing incomplete document ID generation...")
    db = SessionLocal()
    
    try:
        # Create a test incomplete document
        result = kb.add_document(
            filename="test_incomplete.pdf",
            content="Test content",
            doc_type="incomplete",
            project_id=8,
            db=db
        )
        
        if isinstance(result, dict) and result.get("status") == "duplicate":
            print(f"Duplicate document found, using existing: {result}")
            return
            
        # Refresh to get updated data
        db.refresh(result)
        
        print(f"Created document: ID={result.project_specific_id}, Global ID={result.id}")
        print(f"Document type: {result.doc_type}")
        print(f"Project ID: {result.project_id}")
        
        # Verify ID starts from 1
        if result.project_specific_id == 1:
            print("✓ SUCCESS: Document ID starts from 1")
        else:
            print(f"✗ FAILURE: Expected ID=1, got ID={result.project_specific_id}")
            
        return result
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        db.close()

def cleanup_test_doc(doc):
    print("\nCleaning up test document...")
    db = SessionLocal()
    try:
        kb.delete_document(doc.id, db)
        print("✓ Test document deleted")
    except Exception as e:
        print(f"Error during cleanup: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    doc = test_incomplete_doc_id_generation()
    if doc and hasattr(doc, 'id'):
        cleanup_test_doc(doc)