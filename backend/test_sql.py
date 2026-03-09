#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
使用原始SQL查询测试数据库连接和数据
"""

from sqlalchemy import create_engine, text
from core.config import settings

def test_raw_sql():
    """使用原始SQL查询测试数据库"""
    print("=== 原始SQL查询测试 ===")
    
    # 创建数据库引擎
    database_url = settings.DATABASE_URL
    if "mysql" in database_url and "charset=" not in database_url:
        database_url = f"{database_url}?charset=utf8mb4"
    
    print(f"连接URL: {database_url}")
    
    engine = create_engine(
        database_url, 
        pool_pre_ping=True, 
        connect_args={"connect_timeout": 3, "charset": "utf8mb4"}
    )
    
    with engine.connect() as conn:
        # 测试1：显示数据库名称
        print("\n1. 显示当前数据库名称:")
        result = conn.execute(text("SELECT DATABASE();"))
        db_name = result.fetchone()[0]
        print(f"   当前数据库: {db_name}")
        
        # 测试2：查询knowledge_documents表记录数
        print("\n2. 查询knowledge_documents表记录数:")
        result = conn.execute(text("SELECT COUNT(*) FROM knowledge_documents;"))
        count = result.fetchone()[0]
        print(f"   文档总数: {count}")
        
        # 测试3：查询前5条记录
        print("\n3. 查询前5条记录:")
        result = conn.execute(text("SELECT id, project_id, filename, doc_type, created_at FROM knowledge_documents LIMIT 5;"))
        rows = result.fetchall()
        for row in rows:
            print(f"   - ID: {row[0]}, Project ID: {row[1]}, Filename: {row[2]}, Type: {row[3]}, Created: {row[4]}")
        
        # 测试4：查询特定项目的文档
        print("\n4. 查询项目ID为8的文档:")
        result = conn.execute(text("SELECT id, project_id, filename, doc_type FROM knowledge_documents WHERE project_id = 8;"))
        rows = result.fetchall()
        print(f"   项目8的文档数量: {len(rows)}")
        for row in rows[:5]:  # 只显示前5条
            print(f"   - ID: {row[0]}, Project ID: {row[1]}, Filename: {row[2]}, Type: {row[3]}")
        
        # 测试5：检查ORM查询问题
        print("\n5. 测试ORM查询:")
        from sqlalchemy.orm import Session
        from core.models import KnowledgeDocument
        
        with Session(engine) as session:
            # 测试ORM查询
            docs = session.query(KnowledgeDocument).all()
            print(f"   ORM查询返回文档数量: {len(docs)}")
            
            # 测试带条件的ORM查询
            docs_project_8 = session.query(KnowledgeDocument).filter(KnowledgeDocument.project_id == 8).all()
            print(f"   ORM查询项目8的文档数量: {len(docs_project_8)}")
            
            # 测试原始SQL与ORM查询的差异
            result = conn.execute(text("SELECT * FROM knowledge_documents WHERE id = 1;"))
            sql_row = result.fetchone()
            print(f"   原始SQL查询ID=1的文档: {sql_row is not None}")
            
            orm_doc = session.query(KnowledgeDocument).filter(KnowledgeDocument.id == 1).first()
            print(f"   ORM查询ID=1的文档: {orm_doc is not None}")
            
            # 检查映射关系
            if sql_row and orm_doc:
                print(f"   原始SQL字段数: {len(sql_row)}")
                print(f"   模型字段数: {len(KnowledgeDocument.__table__.columns)}")
                print(f"   模型字段: {[col.name for col in KnowledgeDocument.__table__.columns]}")

if __name__ == "__main__":
    test_raw_sql()
