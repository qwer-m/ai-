#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
详细检查MySQL数据库中的数据和表结构
"""

from sqlalchemy import create_engine, text
from core.config import settings

def detailed_check():
    """详细检查数据库"""
    print("=== MySQL数据库详细检查 ===")
    
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
        # 检查所有表
        print("\n1. 检查所有表:")
        result = conn.execute(text("SHOW TABLES;"))
        tables = result.fetchall()
        for table in tables:
            print(f"   - {table[0]}")
        
        # 检查knowledge_documents表结构
        print("\n2. 检查knowledge_documents表结构:")
        result = conn.execute(text("DESCRIBE knowledge_documents;"))
        columns = result.fetchall()
        for column in columns:
            print(f"   - {column[0]}: {column[1]} {column[2]} {column[3]} {column[4]} {column[5]}")
        
        # 直接SQL查询知识文档
        print("\n3. 直接SQL查询knowledge_documents:")
        result = conn.execute(text("SELECT COUNT(*) FROM knowledge_documents;"))
        count = result.fetchone()[0]
        print(f"   文档总数: {count}")
        
        # 查看前5条记录
        print("\n4. 查看前5条记录:")
        result = conn.execute(text("SELECT id, project_id, filename, doc_type, created_at FROM knowledge_documents LIMIT 5;"))
        rows = result.fetchall()
        if rows:
            for row in rows:
                print(f"   - ID: {row[0]}, Project ID: {row[1]}, Filename: {row[2]}, Type: {row[3]}, Created: {row[4]}")
        else:
            print("   没有找到记录")
        
        # 检查是否有其他表中的数据
        print("\n5. 检查其他表数据:")
        result = conn.execute(text("SELECT COUNT(*) FROM projects;"))
        project_count = result.fetchone()[0]
        print(f"   projects表记录数: {project_count}")
        
        result = conn.execute(text("SELECT COUNT(*) FROM test_generations;"))
        test_gen_count = result.fetchone()[0]
        print(f"   test_generations表记录数: {test_gen_count}")
        
        # 检查projects表数据
        print("\n6. 查看projects表数据:")
        result = conn.execute(text("SELECT id, name FROM projects;"))
        projects = result.fetchall()
        for project in projects:
            print(f"   - ID: {project[0]}, Name: {project[1]}")
        
        # 检查knowledge_documents表中的project_id分布
        print("\n7. 查看knowledge_documents表中的project_id分布:")
        result = conn.execute(text("SELECT project_id, COUNT(*) FROM knowledge_documents GROUP BY project_id;"))
        project_dist = result.fetchall()
        for dist in project_dist:
            print(f"   - Project ID: {dist[0]}, Count: {dist[1]}")
        
        # 检查特定项目的文档
        print("\n8. 检查所有项目的文档详情:")
        result = conn.execute(text("SELECT id, project_id, filename, doc_type FROM knowledge_documents;"))
        all_docs = result.fetchall()
        for doc in all_docs:
            print(f"   - ID: {doc[0]}, Project ID: {doc[1]}, Filename: {doc[2]}, Type: {doc[3]}")

if __name__ == "__main__":
    detailed_check()
