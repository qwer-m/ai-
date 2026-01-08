from core.database import SessionLocal
from core.models import Project

# Get database session
db = SessionLocal()

try:
    # Print current projects
    print('Current projects:')
    projects = db.query(Project).all()
    for p in projects:
        print(f'ID: {p.id}, Name: {p.name}, Description: {p.description}, Level: {p.level}')
    
    # Delete projects with invalid names (containing ???)
    dirty_projects = db.query(Project).filter(Project.name.contains('???')).all()
    if dirty_projects:
        print(f'\nFound {len(dirty_projects)} dirty projects to delete:')
        for p in dirty_projects:
            print(f'Deleting project: ID={p.id}, Name={p.name}')
            db.delete(p)
        db.commit()
        print(f'Successfully deleted {len(dirty_projects)} dirty projects.')
    else:
        print('\nNo dirty projects found.')
    
    # Print cleaned projects list
    print('\nCleaned projects:')
    clean_projects = db.query(Project).all()
    for p in clean_projects:
        print(f'ID: {p.id}, Name: {p.name}, Description: {p.description}, Level: {p.level}')
    
finally:
    # Close the database session
    db.close()