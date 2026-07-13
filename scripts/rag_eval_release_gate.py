#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
RAG 发布门禁脚本

用途：
1. 运行 regression 数据集评测并做门禁判定
2. 可选运行 challenge 数据集并输出风险提示
3. 输出简洁指标摘要，便于 CI 直接读取日志

说明：
- 依赖现有 FastAPI 接口：
  - POST /api/auth/token
  - POST /api/rag/eval/run?project_id=...
  - GET  /api/rag/eval/run/{run_id}
- 仅使用 Python 标准库，可直接运行
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


TERMINAL_STATUS = {"success", "failed", "stopped"}


@dataclass
class RunMetrics:
    """单次评测运行的核心指标摘要。"""

    run_id: int
    dataset_id: int
    status: str
    recall_at_5: float
    mrr: float
    answer_correctness: float
    faithfulness: float
    pass_rate: float


class HttpClient:
    """简单 HTTP 客户端：封装 JSON / 表单请求与错误处理。"""

    def __init__(self, base_url: str, token: str | None = None, timeout_sec: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_sec = timeout_sec

    def set_token(self, token: str) -> None:
        """设置 Bearer Token。"""
        self.token = token

    def post_form(self, path: str, form_data: dict[str, Any]) -> dict[str, Any]:
        """发送表单请求，主要用于登录拿 token。"""
        url = f"{self.base_url}{path}"
        payload = urllib.parse.urlencode(form_data).encode("utf-8")
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        return self._request_json("POST", url, payload=payload, headers=headers)

    def post_json(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        """发送 JSON POST 请求。"""
        url = f"{self.base_url}{path}"
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        return self._request_json("POST", url, payload=payload, headers=headers)

    def get_json(self, path: str) -> dict[str, Any]:
        """发送 JSON GET 请求。"""
        url = f"{self.base_url}{path}"
        return self._request_json("GET", url)

    def _request_json(
        self,
        method: str,
        url: str,
        payload: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """统一请求入口，自动附加授权头并解析 JSON。"""
        req_headers = dict(headers or {})
        if self.token:
            req_headers["Authorization"] = f"Bearer {self.token}"

        request = urllib.request.Request(url=url, data=payload, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_sec) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
                if not raw.strip():
                    return {}
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore") if hasattr(e, "read") else ""
            raise RuntimeError(f"HTTP {e.code} {url} -> {body}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"URL error {url}: {e}") from e


def _to_float(value: Any) -> float:
    """安全转换浮点数，避免空值导致脚本中断。"""
    try:
        return float(value)
    except Exception:
        return 0.0


def login_and_get_token(client: HttpClient, username: str, password: str) -> str:
    """通过 /api/auth/token 登录，返回访问令牌。"""
    data = client.post_form("/api/auth/token", {"username": username, "password": password})
    token = str(data.get("access_token") or "").strip()
    if not token:
        raise RuntimeError("登录成功但未拿到 access_token")
    return token


def start_eval_run(
    client: HttpClient,
    *,
    project_id: int,
    dataset_id: int,
    run_name: str,
    config: dict[str, Any] | None = None,
) -> int:
    """启动评测运行，返回 run_id。"""
    path = f"/api/rag/eval/run?project_id={project_id}"
    payload = {"dataset_id": dataset_id, "config": config or {}, "run_name": run_name}
    resp = client.post_json(path, payload)
    run_id = int(resp.get("run_id") or 0)
    if run_id <= 0:
        raise RuntimeError(f"启动评测失败，返回异常：{resp}")
    return run_id


def wait_run_finished(
    client: HttpClient,
    *,
    run_id: int,
    poll_interval_sec: float,
    timeout_sec: int,
) -> dict[str, Any]:
    """轮询运行状态直到结束。"""
    started = time.time()
    last_status = "unknown"

    while True:
        elapsed = time.time() - started
        if elapsed > timeout_sec:
            raise TimeoutError(f"评测运行超时 run_id={run_id}, last_status={last_status}")

        result = client.get_json(f"/api/rag/eval/run/{run_id}")
        run = result.get("run") or {}
        progress = result.get("progress") or {}
        status = str(run.get("status") or progress.get("status") or "unknown")
        last_status = status

        finished = int(progress.get("finished_samples") or 0)
        total = int(progress.get("total_samples") or 0)
        print(f"[run:{run_id}] status={status} progress={finished}/{total}")

        if status in TERMINAL_STATUS:
            return result
        time.sleep(max(0.1, poll_interval_sec))


def parse_run_metrics(run_status_payload: dict[str, Any], dataset_id: int, run_id: int) -> RunMetrics:
    """从运行状态响应提取核心指标。"""
    run = run_status_payload.get("run") or {}
    metrics = run_status_payload.get("metrics") or {}
    overview = metrics.get("overview") or {}

    return RunMetrics(
        run_id=run_id,
        dataset_id=dataset_id,
        status=str(run.get("status") or "unknown"),
        recall_at_5=_to_float(overview.get("recall@5")),
        mrr=_to_float(overview.get("mrr")),
        answer_correctness=_to_float(overview.get("avg_answer_correctness")),
        faithfulness=_to_float(overview.get("avg_faithfulness")),
        pass_rate=_to_float(overview.get("pass_rate")),
    )


def print_summary(title: str, metrics: RunMetrics) -> None:
    """打印简洁摘要，方便 CI 日志阅读。"""
    print("\n" + "=" * 72)
    print(title)
    print("-" * 72)
    print(f"run_id              : {metrics.run_id}")
    print(f"dataset_id          : {metrics.dataset_id}")
    print(f"status              : {metrics.status}")
    print(f"recall@5            : {metrics.recall_at_5:.4f}")
    print(f"mrr                 : {metrics.mrr:.4f}")
    print(f"answer_correctness  : {metrics.answer_correctness:.4f}")
    print(f"faithfulness        : {metrics.faithfulness:.4f}")
    print(f"pass_rate           : {metrics.pass_rate:.4f}")


def evaluate_regression_gate(
    metrics: RunMetrics,
    *,
    recall_threshold: float,
    pass_rate_threshold: float,
    faithfulness_threshold: float,
) -> tuple[bool, list[str]]:
    """按阈值判定 regression gate 是否通过。"""
    reasons: list[str] = []

    if metrics.status != "success":
        reasons.append(f"run status is {metrics.status}, expected success")
    if metrics.recall_at_5 < recall_threshold:
        reasons.append(f"recall@5 {metrics.recall_at_5:.4f} < {recall_threshold:.4f}")
    if metrics.pass_rate < pass_rate_threshold:
        reasons.append(f"pass_rate {metrics.pass_rate:.4f} < {pass_rate_threshold:.4f}")
    if metrics.faithfulness < faithfulness_threshold:
        reasons.append(f"faithfulness {metrics.faithfulness:.4f} < {faithfulness_threshold:.4f}")

    return (len(reasons) == 0), reasons


def evaluate_challenge_risk(
    metrics: RunMetrics,
    *,
    recall_threshold: float,
    pass_rate_threshold: float,
    faithfulness_threshold: float,
) -> list[str]:
    """challenge 数据集只做风险提示，不直接卡发布。"""
    risks: list[str] = []
    if metrics.status != "success":
        risks.append(f"challenge run status={metrics.status}")
    if metrics.recall_at_5 < recall_threshold:
        risks.append(f"challenge recall@5 low: {metrics.recall_at_5:.4f} < {recall_threshold:.4f}")
    if metrics.pass_rate < pass_rate_threshold:
        risks.append(f"challenge pass_rate low: {metrics.pass_rate:.4f} < {pass_rate_threshold:.4f}")
    if metrics.faithfulness < faithfulness_threshold:
        risks.append(f"challenge faithfulness low: {metrics.faithfulness:.4f} < {faithfulness_threshold:.4f}")
    return risks


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数。"""
    parser = argparse.ArgumentParser(description="RAG 评测发布门禁脚本")

    # 必填业务参数
    parser.add_argument("--regression-dataset-id", type=int, required=True, help="回归数据集 ID")
    parser.add_argument("--challenge-dataset-id", type=int, default=None, help="挑战数据集 ID（可选）")
    parser.add_argument("--recall-threshold", type=float, required=True, help="门禁阈值：recall@5")
    parser.add_argument("--pass-rate-threshold", type=float, required=True, help="门禁阈值：pass_rate")
    parser.add_argument("--faithfulness-threshold", type=float, required=True, help="门禁阈值：faithfulness")

    # 连接参数
    parser.add_argument("--base-url", type=str, default=os.getenv("RAG_EVAL_BASE_URL", "http://127.0.0.1:8000"), help="后端地址")
    parser.add_argument("--project-id", type=int, default=int(os.getenv("RAG_EVAL_PROJECT_ID", "0") or "0"), help="项目 ID（接口要求）")

    # 认证参数：token 优先，否则用户名密码登录
    parser.add_argument("--token", type=str, default=os.getenv("RAG_EVAL_TOKEN", ""), help="Bearer Token")
    parser.add_argument("--username", type=str, default=os.getenv("RAG_EVAL_USERNAME", ""), help="用户名")
    parser.add_argument("--password", type=str, default=os.getenv("RAG_EVAL_PASSWORD", ""), help="密码")

    # 轮询参数
    parser.add_argument("--poll-interval-sec", type=float, default=float(os.getenv("RAG_EVAL_POLL_INTERVAL_SEC", "2")), help="轮询间隔秒")
    parser.add_argument("--run-timeout-sec", type=int, default=int(os.getenv("RAG_EVAL_RUN_TIMEOUT_SEC", "1800")), help="单次运行超时时间秒")

    return parser


def ensure_auth(client: HttpClient, args: argparse.Namespace) -> None:
    """保证客户端带有效 token。"""
    token = (args.token or "").strip()
    if token:
        client.set_token(token)
        return

    username = (args.username or "").strip()
    password = str(args.password or "")
    if not username or not password:
        raise RuntimeError("未提供 token，且 username/password 不完整，无法认证")

    token = login_and_get_token(client, username, password)
    client.set_token(token)


def run_dataset(
    client: HttpClient,
    *,
    project_id: int,
    dataset_id: int,
    run_name: str,
    poll_interval_sec: float,
    run_timeout_sec: int,
) -> RunMetrics:
    """启动并等待数据集评测，返回核心指标。"""
    run_id = start_eval_run(
        client,
        project_id=project_id,
        dataset_id=dataset_id,
        run_name=run_name,
        config={},
    )
    payload = wait_run_finished(
        client,
        run_id=run_id,
        poll_interval_sec=poll_interval_sec,
        timeout_sec=run_timeout_sec,
    )
    return parse_run_metrics(payload, dataset_id=dataset_id, run_id=run_id)


def main() -> int:
    """脚本主流程。"""
    args = build_parser().parse_args()

    if int(args.project_id or 0) <= 0:
        print("ERROR: --project-id 未提供或无效（也可通过环境变量 RAG_EVAL_PROJECT_ID 提供）", file=sys.stderr)
        return 2

    client = HttpClient(base_url=args.base_url)

    try:
        ensure_auth(client, args)

        # 1) 运行回归集并做门禁
        regression_metrics = run_dataset(
            client,
            project_id=args.project_id,
            dataset_id=args.regression_dataset_id,
            run_name=f"release-gate-regression-{int(time.time())}",
            poll_interval_sec=args.poll_interval_sec,
            run_timeout_sec=args.run_timeout_sec,
        )
        print_summary("Regression Dataset Summary", regression_metrics)

        gate_passed, gate_fail_reasons = evaluate_regression_gate(
            regression_metrics,
            recall_threshold=args.recall_threshold,
            pass_rate_threshold=args.pass_rate_threshold,
            faithfulness_threshold=args.faithfulness_threshold,
        )

        # 2) 可选运行挑战集，只给风险提示
        challenge_risks: list[str] = []
        if args.challenge_dataset_id:
            challenge_metrics = run_dataset(
                client,
                project_id=args.project_id,
                dataset_id=args.challenge_dataset_id,
                run_name=f"release-gate-challenge-{int(time.time())}",
                poll_interval_sec=args.poll_interval_sec,
                run_timeout_sec=args.run_timeout_sec,
            )
            print_summary("Challenge Dataset Summary", challenge_metrics)
            challenge_risks = evaluate_challenge_risk(
                challenge_metrics,
                recall_threshold=args.recall_threshold,
                pass_rate_threshold=args.pass_rate_threshold,
                faithfulness_threshold=args.faithfulness_threshold,
            )

        # 3) 输出门禁结论
        print("\n" + "=" * 72)
        if gate_passed:
            print("RAG RELEASE GATE: PASS")
        else:
            print("RAG RELEASE GATE: FAIL")
            for r in gate_fail_reasons:
                print(f"- {r}")

        if challenge_risks:
            print("\nCHALLENGE RISK WARNINGS:")
            for risk in challenge_risks:
                print(f"- {risk}")

        # regression 不通过则返回非 0；challenge 仅风险提示，不阻断发布
        return 0 if gate_passed else 1

    except TimeoutError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
