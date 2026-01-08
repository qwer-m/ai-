import urllib.parse
from sqlalchemy import create_engine, text
from core.config import settings
from core.models import *

def init_db():
    # First, let's manually create database if not exists for MySQL
    if "mysql" in settings.DATABASE_URL:
        try:
            # Construct a connection string to the server root (no db selected)
            root_url = f"mysql+pymysql://{settings.DB_USER_ENCODED}:{settings.DB_PASSWORD}@{settings.DB_HOST}:{settings.DB_PORT}/mysql"
            root_engine = create_engine(root_url, connect_args={"connect_timeout": 3})
            
            with root_engine.connect() as conn:
                conn.execute(text(f"CREATE DATABASE IF NOT EXISTS {settings.DB_NAME} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
                print(f"Database '{settings.DB_NAME}' checked/created.")
        except Exception as e:
            print(f"Error creating database: {e}")
            return False
    
    # Now create engine with the correct database URL
    from core.database import Base
    
    # Create engine with the correct database URL
    database_url = settings.DATABASE_URL
    if "mysql" in database_url and "charset=" not in database_url:
        database_url = f"{database_url}?charset=utf8mb4"
    
    engine = create_engine(
        database_url, 
        pool_pre_ping=True, 
        connect_args={"connect_timeout": 3, "charset": "utf8mb4"}
    )
    
    # 2. Create Tables
    print("Creating tables...")
    try:
        Base.metadata.create_all(bind=engine)
        print("Tables created successfully.")
        
        # Check and add 'summary' column if missing (Migration logic)
        with engine.connect() as conn:
            try:
                # This query works for MySQL to check column existence
                check_col = text(f"SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = '{settings.DB_NAME}' AND TABLE_NAME = 'knowledge_documents' AND COLUMN_NAME = 'summary'")
                result = conn.execute(check_col).scalar()
                
                if result == 0:
                    print("Adding 'summary' column to knowledge_documents...")
                    conn.execute(text("ALTER TABLE knowledge_documents ADD COLUMN summary TEXT NULL"))
                    conn.commit()
                    print("Column added.")
                
                # Check for structured_report in api_executions
                check_col_api = text(f"SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = '{settings.DB_NAME}' AND TABLE_NAME = 'api_executions' AND COLUMN_NAME = 'structured_report'")
                result_api = conn.execute(check_col_api).scalar()
                
                if result_api == 0:
                    print("Adding 'structured_report' column to api_executions...")
                    conn.execute(text("ALTER TABLE api_executions ADD COLUMN structured_report JSON NULL"))
                    conn.commit()
                    print("Column added.")
                
                # Check for email in users
                check_col_user = text(f"SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = '{settings.DB_NAME}' AND TABLE_NAME = 'users' AND COLUMN_NAME = 'email'")
                result_user = conn.execute(check_col_user).scalar()
                
                if result_user == 0:
                    print("Adding 'email' and 'is_active' columns to users...")
                    conn.execute(text("ALTER TABLE users ADD COLUMN email VARCHAR(100) NULL"))
                    conn.execute(text("ALTER TABLE users ADD COLUMN is_active TINYINT(1) DEFAULT 1"))
                    conn.commit()
                    print("Columns added to users.")
                
                # Check for user_id in other tables
                tables_to_check = [
                    "projects", "test_generations", "ui_executions", "ui_error_operations",
                    "api_executions", "evaluations", "test_generation_comparisons",
                    "operation_logs", "recall_metrics", "knowledge_documents", "system_configs"
                ]
                
                for table in tables_to_check:
                    try:
                        check_col = text(f"SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = '{settings.DB_NAME}' AND TABLE_NAME = '{table}' AND COLUMN_NAME = 'user_id'")
                        result = conn.execute(check_col).scalar()
                        
                        if result == 0:
                            print(f"Adding 'user_id' column to {table}...")
                            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN user_id INT NULL"))
                            # Add FK constraint if possible, might fail if data exists but user_id is null? No, null is fine.
                            # But we need to make sure foreign key name is unique.
                            conn.execute(text(f"ALTER TABLE {table} ADD CONSTRAINT fk_{table}_users FOREIGN KEY (user_id) REFERENCES users(id)"))
                            conn.commit()
                            print(f"Column added to {table}.")
                    except Exception as e:
                        print(f"Failed to migrate {table}: {e}")
            except Exception as e:
                print(f"Migration check failed (might be non-MySQL or other error): {e}")

    except Exception as e:
        print(f"Error creating tables: {e}")
        return False

    # Create Default Project
    from sqlalchemy.orm import Session
    from core.auth import get_password_hash
    from core.models import Project, User
    
    with Session(engine) as session:
        # Create Default User if not exists
        default_user = session.query(User).filter(User.username == "admin").first()
        if not default_user:
            default_user = User(
                username="admin", 
                email="admin@example.com",
                hashed_password=get_password_hash("w1314521"),
                is_active=True
            )
            session.add(default_user)
            session.commit()
            print("Default User 'admin' created (password: w1314521).")
        else:
             # Update password if exists to ensure it matches requirement
             default_user.hashed_password = get_password_hash("w1314521")
             session.commit()
             print("Default User 'admin' updated (password: w1314521).")
            
        default_project = session.query(Project).filter(Project.name == "Default Project").first()
        if not default_project:
            default_project = Project(
                name="Default Project", 
                description="Default project for initial setup",
                user_id=default_user.id
            )
            session.add(default_project)
            session.commit()
            print("Default Project created.")
    
    return True

if __name__ == "__main__":
    init_db()
