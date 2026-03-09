#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
检查知识库文档是否正确存储到MySQL数据库
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from core.config import settings
from core.models import KnowledgeDocument

def check_documents():
    """检查数据库中的文档"""
    # 创建数据库引擎
    database_url = settings.DATABASE_URL
    if "mysql" in database_url and "charset=" not in database_url:
        database_url = f"{database_url}?charset=utf8mb4"
    
    engine = create_engine(
        database_url, 
        pool_pre_ping=True, 
        connect_args={"connect_timeout": 3, "charset": "utf8mb4"}
    )
    
    print(f"\n连接到数据库: {database_url.split('@')[1].split('/')[0]}/{settings.DB_NAME}")
    
    # 查询文档数量
    with Session(engine) as session:
        # 获取所有文档
        documents = session.query(KnowledgeDocument).all()
        
        print(f"\n知识库文档总数: {len(documents)}")
        
        if documents:
            print("\n文档列表:")
            print("-" * 80)
            print(f"{'ID':<5} {'项目ID':<8} {'文件名':<30} {'文档类型':<15} {'创建时间':<20}")
            print("-" * 80)
            
            for doc in documents:
                # 格式化创建时间
                created_time = doc.created_at.strftime("%Y-%m-%d %H:%M:%S")
                print(f"{doc.id:<5} {doc.project_id:<8} {doc.filename:<30} {doc.doc_type:<15} {created_time:<20}")
        else:
            print("\n数据库中没有找到文档。")
    
    print("\n检查完成！")

if __name__ == "__main__":
    check_documents()
