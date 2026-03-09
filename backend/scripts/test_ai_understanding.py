
import sys
import os
from dotenv import load_dotenv
from sqlalchemy.orm import Session

# Path setup
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(current_dir, "ai_test_platform"))

from core.ai_client import AIClient
from core.database import get_db, SessionLocal
from core.config_manager import config_manager
from core.models import SystemConfig, Project
from core.chroma_client import chroma_client

def main():
    # Setup DB
    db = SessionLocal()
    try:
        # 1. Get Active Config (Prioritize latest active config regardless of user_id)
        # config = config_manager.get_active_config(db) # This prioritizes user_id=None
        config = db.query(SystemConfig).filter(SystemConfig.is_active == 1).order_by(SystemConfig.updated_at.desc(), SystemConfig.id.desc()).first()
        
        if not config:
            print("Error: No active configuration found in database.")
            return

        print(f"Configuration Loaded:")
        print(f"  Provider: {config.provider}")
        print(f"  Model: {config.model_name}")
        print(f"  API Key: {'******' if config.api_key else 'None'}")
        print(f"  Updated At: {config.updated_at}")
        
        # Initialize Client from Config
        client = AIClient.from_config(config)
        print(f"AIClient Initialized with model: {client.model}")

        # 2. Find Project
        project = db.query(Project).filter(Project.name.like("%未来书房%")).first()
        if not project:
            print("Error: Project '未来书房' not found in database.")
            return
        
        print(f"Target Project: {project.name} (ID: {project.id})")

        # 3. RAG Retrieval & Auto-Indexing
        query = "未来书房项目是一个什么样的系统？"
        print(f"\nPerforming RAG Retrieval for query: '{query}'...")
        
        results = chroma_client.search(
            query=query,
            n_results=5,
            where={"project_id": project.id}
        )
        
        # Check if we need to re-index
        if not results or not results.get('documents') or len(results['documents'][0]) == 0:
            print("Warning: No documents found in ChromaDB. Attempting to restore from MySQL...")
            from core.models import KnowledgeDocument
            docs = db.query(KnowledgeDocument).filter(KnowledgeDocument.project_id == project.id).all()
            if docs:
                print(f"Found {len(docs)} documents in MySQL. Indexing to ChromaDB...")
                count = 0
                for doc in docs:
                    if doc.content:
                        chroma_client.add_document(
                            doc_id=str(doc.id),
                            content=doc.content,
                            metadata={"project_id": project.id, "filename": doc.filename, "doc_type": doc.doc_type}
                        )
                        count += 1
                        print(f"  Indexed: {doc.filename}")
                print(f"Restored {count} documents.")
                
                # Search again
                results = chroma_client.search(
                    query=query,
                    n_results=5,
                    where={"project_id": project.id}
                )
            else:
                print("No documents found in MySQL either.")

        context = ""
        if results and results.get('documents') and len(results['documents']) > 0:
            docs = results['documents'][0]
            print(f"Found {len(docs)} relevant document chunks.")
            for i, doc_text in enumerate(docs):
                context += f"--- Document Chunk {i+1} ---\n{doc_text}\n\n"
        else:
            print("Warning: No relevant documents found in ChromaDB. AI will answer based on internal knowledge only.")

        # 4. Generate Response
        prompt = f"""基于以下已知项目文档信息，回答用户的问题。如果信息不足，请说明。

已知信息:
{context}

用户问题:
{query}
"""
        print(f"Sending request to AI model ({client.model})...")
        # client.generate_response(user_input, system_prompt=None, ...)
        response = client.generate_response(user_input=prompt, model=config.model_name)
        
        print("\n" + "="*30)
        print("AI Response:")
        print("="*30)
        print(response)
        print("="*30)

    except Exception as e:
        print(f"\nExecution Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    main()
