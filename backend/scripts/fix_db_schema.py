import sys
import os
from sqlalchemy import create_engine

# Add path
sys.path.append(os.path.join(os.getcwd(), "ai_test_platform"))

from core.database import engine, Base
from core.models import TestGeneration

def fix_schema():
    print("Dropping test_generations table...")
    try:
        TestGeneration.__table__.drop(engine)
        print("Dropped.")
    except Exception as e:
        print(f"Error dropping table (might not exist): {e}")

    print("Recreating test_generations table...")
    TestGeneration.__table__.create(engine)
    print("Recreated.")

if __name__ == "__main__":
    fix_schema()
