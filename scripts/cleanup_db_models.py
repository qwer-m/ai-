
import sys
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Path setup
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(os.path.join(parent_dir, "ai_test_platform"))

from core.database import Base
from core.models import SystemConfig
from core.config import settings

def cleanup_configs():
    engine = create_engine(settings.DATABASE_URL)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # Get the latest active config
        latest_config = session.query(SystemConfig).filter(SystemConfig.is_active == 1).order_by(SystemConfig.updated_at.desc(), SystemConfig.id.desc()).first()
        
        if not latest_config:
            print("No active configuration found. Aborting cleanup to prevent total loss.")
            return

        print(f"Keeping latest active config: ID {latest_config.id} (Model: {latest_config.model_name})")

        # Find all other configs
        others = session.query(SystemConfig).filter(SystemConfig.id != latest_config.id).all()
        
        if not others:
            print("No other configurations to clean up.")
            return

        print(f"Deleting {len(others)} old/inactive configurations...")
        for c in others:
            print(f"  Deleting ID: {c.id} (Model: {c.model_name}, Active: {c.is_active})")
            session.delete(c)
        
        session.commit()
        print("Cleanup complete.")

    except Exception as e:
        session.rollback()
        print(f"Error during cleanup: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    cleanup_configs()
