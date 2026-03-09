
import sys
import os
sys.path.append(os.getcwd())

from sqlalchemy import create_engine, text, inspect
from core.config import settings

def add_columns():
    print(f"Connecting to {settings.DATABASE_URL}")
    engine = create_engine(settings.DATABASE_URL)
    inspector = inspect(engine)
    existing_columns = [col['name'] for col in inspector.get_columns('ui_executions')]
    
    with engine.connect() as conn:
        if 'screenshot_paths' not in existing_columns:
            print("Adding screenshot_paths column...")
            conn.execute(text("ALTER TABLE ui_executions ADD COLUMN screenshot_paths JSON NULL COMMENT '执行过程中的截图路径列表'"))
        
        if 'quality_score' not in existing_columns:
            print("Adding quality_score column...")
            conn.execute(text("ALTER TABLE ui_executions ADD COLUMN quality_score FLOAT NULL COMMENT '自动化脚本质量评分'"))
            
        if 'evaluation_result' not in existing_columns:
            print("Adding evaluation_result column...")
            conn.execute(text("ALTER TABLE ui_executions ADD COLUMN evaluation_result TEXT NULL COMMENT '详细的评估报告'"))
            
        conn.commit()
    print("Migration completed.")

if __name__ == "__main__":
    add_columns()
