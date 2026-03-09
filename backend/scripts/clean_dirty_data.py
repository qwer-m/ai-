import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy.orm import Session
from sqlalchemy import text
from core.database import engine
from core.models import TestGenerationComparison

def clean_dirty_data():
    print("Running version 5.0 - 动态全库全字段深度扫描")
    print("开始排查脏数据...")
    
    import inspect
    from sqlalchemy import String, Text, JSON, types
    from sqlalchemy.sql import cast
    from sqlalchemy.dialects.mysql import LONGTEXT
    import core.models as models_module
    from core.database import Base
    
    # 1. 动态获取所有 ORM 模型
    model_classes = []
    for name, obj in inspect.getmembers(models_module):
        if inspect.isclass(obj) and issubclass(obj, Base) and obj != Base:
            model_classes.append(obj)
            
    print(f"扫描到 {len(model_classes)} 个数据模型: {[m.__name__ for m in model_classes]}")

    with Session(engine) as session:
        total_dirty_count = 0
        checked_fields_count = 0
        
        for Model in model_classes:
            # 获取模型的主键（用于删除）
            # assuming single primary key for simplicity, which is true for this project (id)
            
            from sqlalchemy.inspection import inspect as sa_inspect
            mapper = sa_inspect(Model)
            
            for column in mapper.columns:
                # 检查字段类型是否为文本或JSON
                is_text_type = isinstance(column.type, (String, Text, LONGTEXT))
                is_json_type = isinstance(column.type, JSON)
                
                if is_text_type or is_json_type:
                    checked_fields_count += 1
                    label = f"{Model.__name__}.{column.name}"
                    # print(f"正在检查: {label} ({column.type})")
                    
                    try:
                        query = session.query(Model)
                        
                        if is_json_type:
                            # JSON 类型转为字符串后进行 LIKE 匹配
                            filter_condition = cast(column, types.String).like("%相关文档%")
                        else:
                            filter_condition = column.like("%相关文档%")
                            
                        dirty_records = query.filter(filter_condition).all()
                        
                        count = len(dirty_records)
                        if count > 0:
                            print(f"\n[!!! 发现脏数据 !!!] {label}")
                            print(f"数量: {count} 条")
                            total_dirty_count += count
                            
                            print(f"正在清理 {label} ...")
                            for r in dirty_records:
                                session.delete(r)
                            session.commit()
                            print("清理完成。")
                        
                    except Exception as e:
                        print(f"检查 {label} 时出错: {e}")
                        session.rollback()

        print(f"\n========================================")
        print(f"扫描结束。")
        print(f"共扫描字段: {checked_fields_count} 个")
        print(f"共清理脏数据: {total_dirty_count} 条")
        print(f"========================================")

if __name__ == "__main__":
    clean_dirty_data()
