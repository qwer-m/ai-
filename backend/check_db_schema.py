
import sys
import os
sys.path.append(os.getcwd())

from sqlalchemy import create_engine, inspect
from core.config import settings

def check_schema():
    print(f"Connecting to {settings.DATABASE_URL}")
    engine = create_engine(settings.DATABASE_URL)
    inspector = inspect(engine)
    columns = inspector.get_columns('ui_executions')
    print("Columns in ui_executions:")
    for col in columns:
        print(f"- {col['name']} ({col['type']})")

if __name__ == "__main__":
    check_schema()
