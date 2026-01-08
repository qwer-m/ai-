import sqlalchemy
from core.database import engine
from sqlalchemy import text

def migrate_db():
    print("开始执行数据库迁移...")
    with engine.connect() as conn:
        # 检查字段是否存在，如果不存在则添加
        try:
            # 尝试查询新字段，如果报错则说明不存在
            conn.execute(text("SELECT vl_model_name FROM system_configs LIMIT 1"))
            print("字段 vl_model_name 已存在，跳过。")
        except Exception:
            print("添加字段: vl_model_name")
            conn.execute(text("ALTER TABLE system_configs ADD COLUMN vl_model_name VARCHAR(100) NULL"))
            
        try:
            conn.execute(text("SELECT turbo_model_name FROM system_configs LIMIT 1"))
            print("字段 turbo_model_name 已存在，跳过。")
        except Exception:
            print("添加字段: turbo_model_name")
            conn.execute(text("ALTER TABLE system_configs ADD COLUMN turbo_model_name VARCHAR(100) NULL"))
            
        conn.commit()
    print("数据库迁移完成。")

if __name__ == "__main__":
    migrate_db()
