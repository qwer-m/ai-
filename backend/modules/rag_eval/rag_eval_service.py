from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from core.ai_client import get_client_for_user
from core.database import SessionLocal
from core.models import RagDataset, RagDatasetSample, RagEvalRun, RagEvalSampleResult
from modules.rag_eval.metrics_retrieval import context_precision, context_recall, first_hit_rank, hit_at_k, mrr, recall_at_k
from modules.rag_eval.rag_eval_aggregator import aggregate_run_metrics, estimate_tokens
from modules.rag_eval.rag_failure_analyzer import analyze_failure_reason
from modules.rag_eval.rag_judge_service import judge_answer
from modules.rag_eval.rag_retrieval_service import run_retrieval_debug

_RUN_THREADS: dict[int, threading.Thread] = {}
_RUN_LOCK = threading.Lock()


def start_eval_run(*, db: Session, user_id: int, project_id: int, dataset_id: int, config: dict[str, Any], run_name: str | None) -> RagEvalRun:
    """创建评测运行并异步执行。"""
    dataset = db.query(RagDataset).filter(RagDataset.id == dataset_id, RagDataset.user_id == user_id).first()
    if not dataset:
        raise ValueError("Dataset not found")
    run = RagEvalRun(
        user_id=user_id,
        project_id=project_id,
        dataset_id=dataset_id,
        run_name=run_name or f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        config_json=config or {},
        status="pending",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    _spawn_run_thread(run.id, user_id)
    return run


def stop_eval_run(*, db: Session, user_id: int, run_id: int) -> RagEvalRun:
    """请求停止运行。"""
    run = db.query(RagEvalRun).filter(RagEvalRun.id == run_id, RagEvalRun.user_id == user_id).first()
    if not run:
        raise ValueError("Run not found")
    run.stop_requested = True
    if run.status in {"pending", "running"}:
        run.status = "stopping"
    db.commit()
    db.refresh(run)
    return run


def resume_eval_run(*, db: Session, user_id: int, run_id: int) -> RagEvalRun:
    """断点续跑：从 cursor 继续执行。"""
    run = db.query(RagEvalRun).filter(RagEvalRun.id == run_id, RagEvalRun.user_id == user_id).first()
    if not run:
        raise ValueError("Run not found")
    if run.status in {"pending", "running", "stopping"}:
        raise ValueError("Run is already active")
    run.stop_requested = False
    run.status = "pending"
    run.finished_at = None
    db.commit()
    db.refresh(run)
    _spawn_run_thread(run.id, user_id)
    return run


def _spawn_run_thread(run_id: int, user_id: int) -> None:
    """启动后台评测线程。"""
    t = threading.Thread(target=_execute_run, args=(run_id, user_id), daemon=True, name=f"rag-eval-run-{run_id}")
    with _RUN_LOCK:
        _RUN_THREADS[run_id] = t
    t.start()


def _execute_run(run_id: int, user_id: int) -> None:
    """后台执行 run，支持停止与断点续跑。"""
    db = SessionLocal()
    try:
        run = db.query(RagEvalRun).filter(RagEvalRun.id == run_id, RagEvalRun.user_id == user_id).first()
        if not run:
            return
        run.status = "running"
        if not run.started_at:
            run.started_at = datetime.now()
        db.commit()

        config = dict(run.config_json or {})
        samples = _load_samples(db, run.dataset_id, config)
        run.total_samples = len(samples)
        db.commit()

        for idx, sample in enumerate(samples):
            db.refresh(run)
            if run.stop_requested:
                run.status = "stopped"
                run.finished_at = datetime.now()
                db.commit()
                return
            if idx < int(run.cursor or 0):
                continue
            if _should_skip_sample(db, run.id, sample.id, config):
                run.finished_samples = int(run.finished_samples or 0) + 1
                run.cursor = idx + 1
                db.commit()
                continue
            _process_single_sample(db=db, run=run, sample=sample, config=config, user_id=user_id)
            run.finished_samples = int(run.finished_samples or 0) + 1
            run.cursor = idx + 1
            db.commit()

        run.metrics_json = aggregate_run_metrics(db, run.id)
        run.status = "success"
        run.finished_at = datetime.now()
        db.commit()
    except Exception as e:
        run = db.query(RagEvalRun).filter(RagEvalRun.id == run_id).first()
        if run:
            run.status = "failed"
            run.finished_at = datetime.now()
            run.metrics_json = {"error": str(e)}
            db.commit()
    finally:
        with _RUN_LOCK:
            _RUN_THREADS.pop(run_id, None)
        db.close()


def _load_samples(db: Session, dataset_id: int, config: dict[str, Any]) -> list[RagDatasetSample]:
    """加载样本：支持标签/难度/启用状态/样本ID/样本区间。"""
    ds_cfg = dict(config.get("dataset_selector") or {})
    q = db.query(RagDatasetSample).filter(RagDatasetSample.dataset_id == dataset_id)
    if bool(ds_cfg.get("enabled_only", True)):
        q = q.filter(RagDatasetSample.enabled.is_(True))
    difficulty = str(ds_cfg.get("difficulty") or "all")
    if difficulty != "all":
        q = q.filter(RagDatasetSample.difficulty == difficulty)
    sample_ids = list(ds_cfg.get("sample_ids") or [])
    if sample_ids:
        q = q.filter(RagDatasetSample.id.in_(sample_ids))
    for tag in list(ds_cfg.get("tags") or []):
        q = q.filter(RagDatasetSample.tags.contains([str(tag)]))
    rows = q.order_by(RagDatasetSample.id.asc()).all()

    run_control = dict(config.get("run_control") or {})
    range_text = str(ds_cfg.get("sample_range") or run_control.get("sample_range") or "all").strip().lower()
    if range_text and range_text not in {"all", "*"}:
        try:
            start_idx, end_idx = [int(x) for x in range_text.split("-", 1)]
            if start_idx >= 1 and end_idx >= start_idx:
                rows = rows[start_idx - 1 : end_idx]
        except Exception:
            pass
    return rows


def _should_skip_sample(db: Session, run_id: int, sample_id: int, config: dict[str, Any]) -> bool:
    """仅评测未完成样本：已存在结果时跳过。"""
    run_control = dict(config.get("run_control") or {})
    if not bool(run_control.get("only_unfinished", False)):
        return False
    exists = db.query(RagEvalSampleResult).filter(RagEvalSampleResult.run_id == run_id, RagEvalSampleResult.sample_id == sample_id).first()
    return bool(exists)


def _process_single_sample(*, db: Session, run: RagEvalRun, sample: RagDatasetSample, config: dict[str, Any], user_id: int) -> None:
    """评测单样本并落库。"""
    retrieval_cfg = dict(config.get("retrieval") or {})
    rerank_top_n = int(retrieval_cfg.get("rerank_top_n") or 5)
    model_cfg = dict(config.get("model") or {})
    judge_cfg = dict(config.get("judge") or {})

    started = time.perf_counter()
    retrieval_result = run_retrieval_debug(query=sample.query, project_id=run.project_id, db=db, user_id=user_id, config=config)
    retrieval_latency_ms = float(retrieval_result.get("retrieval_latency_ms") or 0.0)
    context_text = str(retrieval_result.get("context") or "")

    answer_text = ""
    generation_started = time.perf_counter()
    if bool((config.get("advanced") or {}).get("enable_generation", True)) and context_text:
        client = get_client_for_user(user_id, db)
        answer_text = client.generate_response(
            user_input=f"问题：{sample.query}\n\n上下文：\n{context_text}\n\n请基于上下文回答，若信息不足请明确说明。",
            system_prompt="你是RAG问答助手，禁止脱离上下文编造。",
            db=db,
            model=model_cfg.get("llm_model") or None,
            task_type="general",
        )
    generation_latency_ms = (time.perf_counter() - generation_started) * 1000

    reranked_ids = list(retrieval_result.get("reranked_chunk_ids") or [])
    gold_ids = [str(x) for x in (sample.gold_chunks or []) if str(x).strip()]
    r_metrics = {
        "recall@1": recall_at_k(reranked_ids, gold_ids, 1),
        "recall@3": recall_at_k(reranked_ids, gold_ids, 3),
        "recall@5": recall_at_k(reranked_ids, gold_ids, 5),
        "recall@10": recall_at_k(reranked_ids, gold_ids, 10),
        "hit@1": hit_at_k(reranked_ids, gold_ids, 1),
        "hit@5": hit_at_k(reranked_ids, gold_ids, 5),
        "mrr": mrr(reranked_ids, gold_ids),
        "first_hit_rank": first_hit_rank(reranked_ids, gold_ids),
        "context_precision": context_precision(reranked_ids, gold_ids, max(1, int(retrieval_cfg.get("top_k") or 5))),
        "context_recall": context_recall(reranked_ids, gold_ids, max(1, int(retrieval_cfg.get("top_k") or 5))),
    }
    judge_result = judge_answer(
        query=sample.query,
        gold_answer=sample.gold_answer or "",
        answer_points=list(sample.answer_points or []),
        context=context_text,
        model_answer=answer_text,
        db=db,
        user_id=user_id,
        mode=str(judge_cfg.get("answer_eval_mode") or "hybrid"),
        judge_model=model_cfg.get("judge_model"),
    )
    reason, detail = analyze_failure_reason(
        sample={"gold_chunks": gold_ids, "expected_doc_version": sample.expected_doc_version},
        retrieved_chunks=list(retrieval_result.get("retrieved_chunks") or []),
        reranked_chunks=list(retrieval_result.get("reranked_chunks") or []),
        answer=answer_text,
        metrics={**r_metrics, **judge_result},
        judge_result=judge_result,
        rerank_top_n=rerank_top_n,
    )

    row = db.query(RagEvalSampleResult).filter(RagEvalSampleResult.run_id == run.id, RagEvalSampleResult.sample_id == sample.id).first()
    if not row:
        row = RagEvalSampleResult(run_id=run.id, sample_id=sample.id)
        db.add(row)
    row.retrieved_chunks = retrieval_result.get("retrieved_chunks") or []
    row.reranked_chunks = retrieval_result.get("reranked_chunks") or []
    row.first_hit_rank = r_metrics["first_hit_rank"]
    row.recall_hit = bool((r_metrics["hit@5"] or 0) > 0)
    row.answer_text = answer_text
    row.answer_correct = bool(judge_result.get("is_answer_correct"))
    row.answer_correctness_score = float(judge_result.get("answer_correctness_score") or 0.0)
    row.faithfulness_score = float(judge_result.get("faithfulness_score") or 0.0)
    row.context_precision = float(r_metrics["context_precision"])
    row.context_recall = float(r_metrics["context_recall"])
    row.failure_reason = None if row.answer_correct else reason
    row.failure_detail = None if row.answer_correct else detail
    row.retrieval_latency_ms = retrieval_latency_ms
    row.generation_latency_ms = generation_latency_ms
    row.latency_ms = (time.perf_counter() - started) * 1000
    row.token_usage_json = estimate_tokens(context_text, answer_text)
    row.cost_json = {"total_cost": 0.0, "currency": "CNY", "estimated": True}
    row.detail_json = {
        "retrieval_metrics": r_metrics,
        "judge_result": judge_result,
        "debug": retrieval_result.get("debug") or {},
        "tags": sample.tags or [],
        "sample": {
            "query": sample.query,
            "gold_docs": sample.gold_docs or [],
            "gold_chunks": sample.gold_chunks or [],
            "gold_answer": sample.gold_answer or "",
            "answer_points": sample.answer_points or [],
            "difficulty": sample.difficulty,
        },
    }

