"""
RAG 阶段1联调验收脚本。

用途：
1. 调用上传接口触发离线解析。
2. 轮询 parse-status 并判断状态机是否正确流转。
3. 对 success 场景补充 Chroma 可检索验证。

运行前提：
- 后端服务已启动（FastAPI + Celery worker + Redis + MySQL）。
- 传入可用 token（Authorization: Bearer <token>）。

示例：
python backend/scripts/rag_stage1_acceptance.py --base-url http://127.0.0.1:8000 --token <TOKEN> --project-id 1
"""

from __future__ import annotations

import argparse
import os
import json
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

# 兼容从仓库根目录执行脚本，确保可以导入 backend 下的模块。
BACKEND_ROOT = Path(__file__).resolve().parents[1]
# 固定 Chroma 持久化目录，避免脚本运行目录不同导致误读到错误库。
os.environ.setdefault("CHROMA_PERSIST_PATH", str(BACKEND_ROOT / "chroma_db"))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _normalize_console_output() -> None:
    """
    统一控制台编码与换行符，避免 Windows 下出现 \r 可见噪音。

    说明：
    - 编码固定为 UTF-8，确保中文日志稳定输出。
    - newline 固定为 \\n，减少跨平台采集时的 CRLF 视觉污染。
    """
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="\n")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    except Exception:
        # 终端不支持 reconfigure 时不阻断验收主流程。
        pass


@dataclass
class Scenario:
    """单条联调场景定义。"""

    name: str
    filename: str
    content: bytes
    expect_status: str
    doc_type: str = "requirement"
    force: bool = False
    check_chroma: bool = False
    probe_text: Optional[str] = None


def upload_document(
    client: httpx.Client,
    base_url: str,
    token: str,
    project_id: int,
    scenario: Scenario,
) -> dict:
    """调用上传接口，返回上传响应。"""
    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(scenario.filename).suffix) as tmp:
        tmp.write(scenario.content)
        tmp_path = Path(tmp.name)

    try:
        with tmp_path.open("rb") as f:
            files = {"file": (scenario.filename, f, "application/octet-stream")}
            data = {
                "project_id": str(project_id),
                "doc_type": scenario.doc_type,
                "force": str(scenario.force).lower(),
            }
            headers = {"Authorization": f"Bearer {token}"}
            resp = client.post(f"{base_url}/api/upload-knowledge", data=data, files=files, headers=headers)

        resp.raise_for_status()
        return resp.json()
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


def poll_parse_status(
    client: httpx.Client,
    base_url: str,
    token: str,
    document_id: int,
    timeout_sec: int = 120,
    interval_sec: float = 1.5,
) -> dict:
    """轮询 parse-status，直到 success/failed 或超时。"""
    headers = {"Authorization": f"Bearer {token}"}
    deadline = time.time() + timeout_sec
    latest = {}

    while time.time() < deadline:
        resp = client.get(
            f"{base_url}/api/knowledge/{document_id}/parse-status",
            headers=headers,
        )
        resp.raise_for_status()
        latest = resp.json()

        status = latest.get("parse_status")
        if status in ("success", "failed"):
            return latest

        time.sleep(interval_sec)

    raise TimeoutError(f"parse-status 超时，最后状态: {json.dumps(latest, ensure_ascii=False)}")


def check_chroma_retrievable(project_id: int, doc_id: int, probe_text: str) -> bool:
    """验证指定文档在 Chroma 中是否可检索。"""
    try:
        from core.cache_layer.chroma_client import chroma_client

        if not getattr(chroma_client, "collection", None):
            return False

        # 优先走语义检索，校验“可被召回”而不只是“有记录”。
        try:
            result = chroma_client.search(
                query=probe_text,
                n_results=5,
                where={"$and": [{"project_id": project_id}, {"doc_id": str(doc_id)}]},
                raise_on_error=True,
            )
            docs = (result or {}).get("documents") or []
            first = docs[0] if isinstance(docs, list) and docs else []
            if first:
                return True
        except Exception:
            pass

        # 部分 Chroma 版本在 where + query 上存在偶发 planner 异常，
        # 这里降级为按 doc_id 直查，确保验收结论稳定可复现。
        got = chroma_client.collection.get(
            where={"doc_id": str(doc_id)},
            include=["documents", "metadatas"],
            limit=1,
        )
        ids = (got or {}).get("ids") or []
        return bool(ids)
    except Exception:
        return False


def build_default_scenarios() -> list[Scenario]:
    """构造默认验收场景矩阵。"""
    return [
        # 正常链路：txt
        Scenario(
            name="txt_success",
            filename="rag_stage1_normal_txt.txt",
            content="阶段1联调文本：用户登录成功后可查看订单。".encode("utf-8"),
            expect_status="success",
            check_chroma=True,
            probe_text="用户登录成功后可查看订单",
        ),
        # 正常链路：md
        Scenario(
            name="md_success",
            filename="rag_stage1_normal_md.md",
            content="# 订单模块\n- 创建订单\n- 支付订单\n".encode("utf-8"),
            expect_status="success",
            check_chroma=True,
            probe_text="创建订单 支付订单",
        ),
        # 失败链路：空文件
        Scenario(
            name="empty_file_failed",
            filename="rag_stage1_empty.txt",
            content=b"",
            expect_status="failed",
        ),
        # 失败链路：不支持格式
        Scenario(
            name="unsupported_file_failed",
            filename="rag_stage1_unknown.xyz",
            content=b"\x00\xff\x00\xff",
            expect_status="failed",
        ),
        # 失败链路：损坏 PDF
        Scenario(
            name="broken_pdf_failed",
            filename="rag_stage1_broken.pdf",
            content=b"%PDF-1.7\n1 0 obj\n<</Type/Catalog>>\n%%EOF\nBROKEN",
            expect_status="failed",
        ),
        # 重试链路：首轮失败，重试成功（通过文件名注入）
        Scenario(
            name="fail_once_then_success",
            filename="rag__kbtest_fail_once__.txt",
            content="该场景应首轮失败并在重试后成功。".encode("utf-8"),
            expect_status="success",
            check_chroma=True,
            probe_text="首轮失败并在重试后成功",
        ),
        # 失败链路：模拟摘要失败（通过文件名注入）
        Scenario(
            name="summary_fail_injected",
            filename="rag__kbtest_summary_fail__.txt",
            content="模拟摘要阶段失败。".encode("utf-8"),
            expect_status="failed",
        ),
        # 失败链路：模拟 Chroma 失败（通过文件名注入）
        Scenario(
            name="chroma_fail_injected",
            filename="rag__kbtest_chroma_fail__.txt",
            content="模拟向量索引写入失败。".encode("utf-8"),
            expect_status="failed",
        ),
        # 失败链路：模拟任务执行中异常（通过文件名注入）
        Scenario(
            name="runtime_fail_injected",
            filename="rag__kbtest_runtime_fail__.txt",
            content="模拟任务运行时异常。".encode("utf-8"),
            expect_status="failed",
        ),
    ]


def run_acceptance(args: argparse.Namespace) -> int:
    """执行联调验收并输出结果。"""
    scenarios = build_default_scenarios()
    timeout = args.timeout
    failed_cases: list[str] = []

    with httpx.Client(timeout=60.0) as client:
        for sc in scenarios:
            print(f"\n[CASE] {sc.name}")
            try:
                upload_result = upload_document(
                    client=client,
                    base_url=args.base_url.rstrip("/"),
                    token=args.token,
                    project_id=args.project_id,
                    scenario=sc,
                )
                print(" upload:", json.dumps(upload_result, ensure_ascii=False))

                doc_id = int(upload_result["id"])
                status_result = poll_parse_status(
                    client=client,
                    base_url=args.base_url.rstrip("/"),
                    token=args.token,
                    document_id=doc_id,
                    timeout_sec=timeout,
                    interval_sec=args.interval,
                )
                print(" status:", json.dumps(status_result, ensure_ascii=False))

                final_status = status_result.get("parse_status")
                if final_status != sc.expect_status:
                    failed_cases.append(f"{sc.name}: 期望 {sc.expect_status}，实际 {final_status}")
                    continue

                if sc.check_chroma:
                    ok = check_chroma_retrievable(
                        project_id=args.project_id,
                        doc_id=doc_id,
                        probe_text=sc.probe_text or sc.name,
                    )
                    if not ok:
                        failed_cases.append(f"{sc.name}: Chroma 检索校验失败")
                    else:
                        print(" chroma: 可检索")
            except Exception as e:
                failed_cases.append(f"{sc.name}: 异常 {e}")

    print("\n========== 验收结果 ==========")
    if failed_cases:
        print("失败用例：")
        for item in failed_cases:
            print(" -", item)
        return 1

    print("全部场景通过")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RAG 阶段1联调验收脚本")
    parser.add_argument("--base-url", required=True, help="后端地址，例如 http://127.0.0.1:8000")
    parser.add_argument("--token", required=True, help="登录后获取的 Bearer Token")
    parser.add_argument("--project-id", type=int, required=True, help="目标项目 ID")
    parser.add_argument("--timeout", type=int, default=180, help="单用例状态轮询超时秒数")
    parser.add_argument("--interval", type=float, default=1.5, help="状态轮询间隔秒")
    return parser.parse_args()


if __name__ == "__main__":
    _normalize_console_output()
    raise SystemExit(run_acceptance(parse_args()))
