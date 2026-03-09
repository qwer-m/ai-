
import sys
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from datetime import datetime

# Path setup
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(os.path.join(parent_dir, "ai_test_platform"))

from core.database import Base
from core.models import SystemConfig
from core.config import settings

def inspect_configs():
    engine = create_engine(settings.DATABASE_URL)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        print("Checking SystemConfig table...")
        configs = session.query(SystemConfig).all()
        if not configs:
            print("No configurations found.")
        else:
            print(f"Found {len(configs)} configurations:")
            for c in configs:
                status = "ACTIVE" if c.is_active else "INACTIVE"
                print(f"ID: {c.id}, Provider: {c.provider}, Model: {c.model_name}, Status: {status}, Updated: {c.updated_at}")
    finally:
        session.close()

if __name__ == "__main__":
    inspect_configs()
