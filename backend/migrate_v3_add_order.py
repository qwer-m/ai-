from core.database import engine
from sqlalchemy import text

def migrate():
    print("Migrating database for Knowledge Base ordering...")
    with engine.connect() as conn:
        try:
            # Check if column exists
            conn.execute(text("SELECT display_order FROM knowledge_documents LIMIT 1"))
            print("Column 'display_order' already exists in 'knowledge_documents'.")
        except Exception:
            print("Adding column 'display_order' to 'knowledge_documents'...")
            try:
                # Add column
                conn.execute(text("ALTER TABLE knowledge_documents ADD COLUMN display_order FLOAT DEFAULT 0"))
                conn.commit()
                print("Column added successfully.")
            except Exception as e:
                print(f"Failed to add column: {e}")
        
        # Populate display_order with id to ensure unique and time-ordered initial state
        print("Populating display_order with id...")
        conn.execute(text("UPDATE knowledge_documents SET display_order = id WHERE display_order = 0"))
        conn.commit()
        print("Population complete.")

if __name__ == "__main__":
    migrate()
