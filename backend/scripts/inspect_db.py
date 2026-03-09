
import sys
import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Path setup
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(current_dir, "ai_test_platform"))

from core.config import settings

def main():
    engine = create_engine(settings.DATABASE_URL)
    with engine.connect() as conn:
        print("--- System Configs ---")
        result = conn.execute(text("SELECT id, user_id, provider, model_name, is_active, updated_at FROM system_configs ORDER BY id DESC"))
        for row in result:
            print(row)
            
        print("\n--- Project 8 Documents ---")
        result = conn.execute(text("SELECT id, filename, doc_type, length(content) as len FROM knowledge_documents WHERE project_id = 8"))
        rows = list(result)
        if not rows:
            print("No documents found for project 8.")
        else:
            for row in rows:
                print(row)

if __name__ == "__main__":
    main()
