"""
任务模块 (Tasks Module)

该模块定义了 Celery 异步任务，用于处理耗时操作和定时维护任务。
主要功能：
1. 异步生成测试用例 (generate_test_cases_task)。
2. 清理过期日志 (cleanup_logs_task)。
3. 归档旧数据 (archive_old_data_task)。

调用关系：
- 调用 `modules.test_generation.test_generator` 执行具体的生成逻辑。
- 操作 `core.models` 中的数据库模型 (LogEntry, TestGeneration) 进行数据维护。
"""

from celery_config import celery_app
from modules.test_generation import test_generator
from core.database import SessionLocal
import json

@celery_app.task(bind=True, name="modules.tasks.generate_test_cases_task")
def generate_test_cases_task(self, requirement: str, project_id: int, doc_type: str = "requirement", compress: bool = False, expected_count: int = 20, batch_index: int = 0, batch_size: int = 20, user_id: int = None):
    """
    异步生成测试用例任务 (Async Test Generation Task)
    
    后台执行测试用例生成，避免阻塞 API 请求。
    
    Args:
        requirement: 需求文本。
        project_id: 项目 ID。
        doc_type: 文档类型。
        compress: 是否压缩上下文。
        expected_count: 期望生成的数量。
        batch_index: 批次索引。
        batch_size: 批次大小。
        user_id: 用户 ID。
        
    Returns:
        dict: 生成结果。
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

@celery_app.task(bind=True, name="modules.tasks.cleanup_logs_task")
def cleanup_logs_task(self, retention_hours: int = 72):
    """
    清理日志任务 (Cleanup Logs Task)
    
    定期删除过期的日志记录。
    
    Args:
        retention_hours: 保留时间 (小时)，默认 72 小时。
    """
    from core.models import LogEntry
    from datetime import datetime, timedelta
    
    db = SessionLocal()
    cutoff_date = datetime.utcnow() - timedelta(hours=retention_hours)
    
    try:
        # Bulk delete is more efficient
        deleted_count = db.query(LogEntry).filter(LogEntry.created_at < cutoff_date).delete(synchronize_session=False)
        db.commit()
        return {"deleted_logs": deleted_count}
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()

@celery_app.task(bind=True, name="modules.tasks.archive_old_data_task")
def archive_old_data_task(self, retention_days: int = 30):
    """
    归档旧数据任务 (Archive Old Data Task)
    
    将超过保留期限的 LogEntry 和 TestGeneration 数据导出为 JSON 文件并从数据库删除。
    
    Args:
        retention_days: 保留时间 (天)，默认 30 天。
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
