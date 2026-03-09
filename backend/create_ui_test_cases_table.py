import sys
import os
sys.path.append(os.getcwd())

from sqlalchemy import create_engine, text
from core.config import settings

def create_table():
    print(f"Connecting to {settings.DATABASE_URL}")
    engine = create_engine(settings.DATABASE_URL)
    
    with engine.connect() as conn:
        print("Creating ui_test_cases table...")
        # Create table SQL
        sql = """
        CREATE TABLE IF NOT EXISTS ui_test_cases (
            id INT AUTO_INCREMENT PRIMARY KEY,
            project_id INT,
            name VARCHAR(100) NOT NULL COMMENT '名称',
            description VARCHAR(255) COMMENT '描述',
            type VARCHAR(20) NOT NULL DEFAULT 'file' COMMENT '类型 (folder/file)',
            parent_id INT COMMENT '父节点ID',
            script_content TEXT COMMENT 'Python脚本内容',
            requirements TEXT COMMENT '关联的测试需求/用例描述',
            automation_type VARCHAR(20) DEFAULT 'web' COMMENT '自动化类型 (web/app)',
            target_config VARCHAR(255) COMMENT '目标URL或AppID',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY (parent_id) REFERENCES ui_test_cases(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """
        conn.execute(text(sql))
        conn.commit()
        print("Table created successfully.")

if __name__ == "__main__":
    create_table()
