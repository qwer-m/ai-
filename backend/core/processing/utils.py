"""平台共用日志记录。"""

import logging
from typing import Optional

from sqlalchemy.orm import Session

from core.db.model_defs import LogEntry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ai_test_platform")


def log_to_db(db: Session, project_id: int, log_type: str, message: str, user_id: Optional[int] = None):
    try:
        log_entry = LogEntry(
            project_id=project_id,
            log_type=log_type,
            message=message,
            user_id=user_id
        )
        db.add(log_entry)
        db.commit()
    except Exception as e:
        logger.error(f"Failed to log to DB: {e}")
