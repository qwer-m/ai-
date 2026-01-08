import sys
import os

# Ensure we can import from the current directory
sys.path.append(os.getcwd())

from sqlalchemy.orm import Session
from sqlalchemy import text
from core.database import engine
from core.models import User, Project, TestGeneration, UIExecution, UIErrorOperation, APIExecution, Evaluation, TestGenerationComparison, LogEntry, RecallMetric, KnowledgeDocument, SystemConfig
from core.auth import get_password_hash

def assign_data_to_admin():
    print("Starting data assignment to default admin...")
    
    with Session(engine) as session:
        # 1. Get or Create Admin User
        admin_username = "admin"
        admin_password = "w1314521" # Password provided by user
        
        user = session.query(User).filter(User.username == admin_username).first()
        
        if not user:
            print(f"User '{admin_username}' not found. Creating...")
            user = User(
                username=admin_username,
                email="admin@example.com", # Default email
                hashed_password=get_password_hash(admin_password),
                is_active=True
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            print(f"Created user '{admin_username}' with ID: {user.id}")
        else:
            print(f"User '{admin_username}' found (ID: {user.id}). Updating password...")
            user.hashed_password = get_password_hash(admin_password)
            user.is_active = True
            session.commit()
            session.refresh(user)
            print("Password updated.")

        admin_id = user.id
        
        # 2. Update Tables
        # List of models to update
        models_to_update = [
            Project,
            TestGeneration,
            UIExecution,
            UIErrorOperation,
            APIExecution,
            Evaluation,
            TestGenerationComparison,
            LogEntry,
            RecallMetric,
            KnowledgeDocument,
            SystemConfig
        ]
        
        for model in models_to_update:
            table_name = model.__tablename__
            print(f"Updating table '{table_name}'...")
            
            # Update records where user_id is NULL
            # Note: We use query.filter(model.user_id == None)
            
            # Count records to be updated
            count = session.query(model).filter(model.user_id == None).count()
            
            if count > 0:
                session.query(model).filter(model.user_id == None).update({model.user_id: admin_id}, synchronize_session=False)
                session.commit()
                print(f"  -> Updated {count} records in '{table_name}' to user_id={admin_id}.")
            else:
                print(f"  -> No orphaned records found in '{table_name}'.")

    print("Data assignment completed successfully.")

if __name__ == "__main__":
    assign_data_to_admin()
