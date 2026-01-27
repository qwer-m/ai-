from core.database import SessionLocal
from core.models import Project

# 获取数据库会话
db = SessionLocal()

try:
    # 打印当前所有项目
    print('Current projects (当前项目列表):')
    projects = db.query(Project).all()
    for p in projects:
        print(f'ID: {p.id}, Name: {p.name}, Description: {p.description}, Level: {p.level}')
    
    # 删除名称无效的项目 (包含 ??? 的乱码项目)
    dirty_projects = db.query(Project).filter(Project.name.contains('???')).all()
    if dirty_projects:
        print(f'\nFound {len(dirty_projects)} dirty projects to delete (发现 {len(dirty_projects)} 个乱码项目待删除):')
        for p in dirty_projects:
            print(f'Deleting project: ID={p.id}, Name={p.name}')
            db.delete(p)
        db.commit()
        print(f'Successfully deleted {len(dirty_projects)} dirty projects (成功删除 {len(dirty_projects)} 个项目).')
    else:
        print('\nNo dirty projects found (未发现乱码项目).')
    
    # 打印清理后的项目列表
    print('\nCleaned projects (清理后的项目列表):')
    clean_projects = db.query(Project).all()
    for p in clean_projects:
        print(f'ID: {p.id}, Name: {p.name}, Description: {p.description}, Level: {p.level}')
    
finally:
    # 关闭数据库会话
    db.close()