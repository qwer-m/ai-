from celery_config import celery_app
from modules.test_generation import test_generator
from core.database import SessionLocal
import json

@celery_app.task(bind=True, name="modules.tasks.generate_test_cases_task")
def generate_test_cases_task(self, requirement: str, project_id: int, doc_type: str = "requirement", compress: bool = False, expected_count: int = 20, batch_index: int = 0, batch_size: int = 20, user_id: int = None):
    """
    Celery task for generating test cases.
    """
    db = SessionLocal()
    try:
        # Update task state to STARTED
        self.update_state(state='STARTED', meta={'status': 'Generating test cases...'})
        
        # Call the synchronous generator
        result = test_generator.generate_test_cases_json(
            requirement=requirement,
            project_id=project_id,
            db=db,
            doc_type=doc_type,
            compress=compress,
            expected_count=expected_count,
            batch_index=batch_index,
            batch_size=batch_size,
            user_id=user_id
        )
        
        return result
    except Exception as e:
        self.update_state(state='FAILURE', meta={'error': str(e)})
        raise e
    finally:
        db.close()

@celery_app.task(bind=True, name="modules.tasks.archive_old_data_task")
def archive_old_data_task(self, retention_days: int = 30):
    """
    Archive and delete data older than retention_days.
    Targets: LogEntry, TestGeneration
    """
    from core.models import LogEntry, TestGeneration
    from datetime import datetime, timedelta
    import os
    import json
    
    db = SessionLocal()
    cutoff_date = datetime.utcnow() - timedelta(days=retention_days)
    archive_dir = "archive_data"
    os.makedirs(archive_dir, exist_ok=True)
    
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    report = {"archived_logs": 0, "archived_tests": 0, "status": "success"}
    
    try:
        # 1. Archive Logs
        logs = db.query(LogEntry).filter(LogEntry.created_at < cutoff_date).all()
        if logs:
            log_data = [{
                "id": l.id, 
                "project_id": l.project_id, 
                "type": l.log_type, 
                "msg": l.message, 
                "created_at": str(l.created_at)
            } for l in logs]
            
            with open(os.path.join(archive_dir, f"logs_{timestamp}.json"), "w", encoding="utf-8") as f:
                json.dump(log_data, f, ensure_ascii=False, indent=2)
                
            # Batch delete
            for l in logs:
                db.delete(l)
            report["archived_logs"] = len(logs)

        # 2. Archive Test Generations
        tests = db.query(TestGeneration).filter(TestGeneration.created_at < cutoff_date).all()
        if tests:
            test_data = [{
                "id": t.id,
                "project_id": t.project_id,
                "requirement": t.requirement_text[:100] + "...", # Truncate for summary
                "result_preview": (t.generated_result or "")[:100],
                "created_at": str(t.created_at)
            } for t in tests]
            
            with open(os.path.join(archive_dir, f"tests_{timestamp}.json"), "w", encoding="utf-8") as f:
                json.dump(test_data, f, ensure_ascii=False, indent=2)
                
            for t in tests:
                db.delete(t)
            report["archived_tests"] = len(tests)

        db.commit()
        return report
        
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
