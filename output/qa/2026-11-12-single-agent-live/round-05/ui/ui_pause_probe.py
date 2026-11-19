"""真实暂停审批只读验收；不填写答复、不提交审批、不控制任务。"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / "backend"))
from core.authn.auth import create_access_token
from core.db.database import SessionLocal
from core.db.model_defs import User, AgentNodeRun
from sqlalchemy import select, func
from playwright.sync_api import sync_playwright, expect

OUTPUT = Path(__file__).resolve().parent
REPORT = {"run_id": 53, "approval_id": 13, "project_id": 8, "document_id": 287,
    "started_at": datetime.now(timezone.utc).isoformat(), "console_errors": [], "page_errors": [],
    "http_errors": [], "request_failures": [], "business_write_requests": [], "api_checks": [], "steps": [], "screenshots": [],
    "network_interception": False, "answers_entered": False, "approval_submitted": False}

def screenshot(page, name):
    path = OUTPUT / name
    page.screenshot(path=str(path), full_page=True)
    REPORT["screenshots"].append(str(path))

def budget_pending(value, path=""):
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            subpath = path + "." + key
            if key == "budget_pending_requests": found.append({"path": subpath, "value": item})
            found.extend(budget_pending(item, subpath))
    elif isinstance(value, list):
        for i, item in enumerate(value): found.extend(budget_pending(item, f"{path}[{i}]"))
    return found

def read_detail(context, token, label):
    response = context.request.get("http://127.0.0.1:8000/api/agents/runs/53", headers={"Authorization": "Bearer " + token}, timeout=30000)
    assert response.status == 200
    payload = response.json()
    run = payload.get("run", payload)
    approval = next(a for a in run["approvals"] if a["id"] == 13)
    # 公开API不暴露sdk_state；只读当前节点预算标量，避免展开完整执行状态。
    with SessionLocal() as db:
        row = db.execute(select(AgentNodeRun.id, func.json_extract(AgentNodeRun.sdk_state, "$.action_checkpoint.budget.pending_requests"))
            .where(AgentNodeRun.run_id == 53).order_by(AgentNodeRun.id.desc()).limit(1)).one()
        pending_value = json.loads(row[1]) if isinstance(row[1], str) else row[1]
        pending = [{"source": "database_read_only", "node_id": row[0], "path": "sdk_state.action_checkpoint.budget.pending_requests", "value": pending_value}]
        db.rollback()
    REPORT["api_checks"].append({"label": label, "path": "/api/agents/runs/53", "status": response.status,
        "run_id": run["id"], "run_status": run["status"], "approval_id": approval["id"], "approval_status": approval["status"],
        "items": approval["request_payload"]["items"], "budget_pending": pending,
        "nodes": [{k: n.get(k) for k in ("id", "node_key", "attempt", "status")} for n in run["nodes"]]})
    assert run["id"] == 53 and run["status"] == "waiting_approval"
    assert approval["status"] == "pending" and len(approval["request_payload"]["items"]) == 2
    assert pending and all(item["value"] == 0 for item in pending)
    return approval

def inspect_questions(page, approval, label):
    page.get_by_role("button", name="确认规则", exact=True).click()
    dialog = page.get_by_role("dialog", name="确认需求规则", exact=True)
    expect(dialog).to_be_visible(timeout=30000)
    expect(dialog.locator(".agent-clarification-step")).to_have_count(2)
    questions = []
    for index, item in enumerate(approval["request_payload"]["items"]):
        dialog.locator(".agent-clarification-step").nth(index).click()
        expect(dialog.locator("#agent-conflict-question")).to_have_text(item["coverage_fragment"])
        expect(dialog.get_by_role("textbox", name="你的答复", exact=True)).to_have_value("")
        expect(dialog.get_by_role("button", name="下一项", exact=False)).to_be_disabled()
        summary = dialog.locator(".agent-clarification-evidence summary")
        if summary.count(): summary.click()
        questions.append({"id": item["id"], "coverage_fragment": dialog.locator("#agent-conflict-question").inner_text(),
            "reason": dialog.locator(".agent-clarification-reason").inner_text(),
            "evidence_text": dialog.locator(".agent-clarification-evidence").inner_text(), "answer_empty": True})
        screenshot(page, f"{label}-question-{index + 1}.png")
    REPORT["steps"].append({"name": label, "approval_id": approval["id"], "questions": questions})
    return questions

def main():
    with SessionLocal() as db:
        token = create_access_token({"sub": db.get(User, 1).username}, expires_delta=timedelta(minutes=15))
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 1100}, locale="zh-CN")
        context.add_init_script("(() => { localStorage.setItem('token', " + json.dumps(token) + "); localStorage.setItem('currentProjectId', '8'); localStorage.setItem('currentActiveTab', 'agent-generation'); })()")
        page = context.new_page()
        page.on("console", lambda msg: REPORT["console_errors"].append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda error: REPORT["page_errors"].append(str(error)))
        page.on("requestfailed", lambda request: REPORT["request_failures"].append({"url": request.url, "method": request.method, "failure": request.failure}))
        page.on("response", lambda response: REPORT["http_errors"].append({"url": response.url, "status": response.status}) if response.status >= 400 else None)
        page.on("request", lambda request: REPORT["business_write_requests"].append({"url": request.url, "method": request.method}) if request.method not in {"GET", "HEAD", "OPTIONS"} else None)
        try:
            for suffix in ("summary", "metrics", "events"):
                response = context.request.get(f"http://127.0.0.1:8000/api/agents/runs/53/{suffix}", headers={"Authorization": "Bearer " + token}, timeout=30000)
                data = response.json()
                REPORT["api_checks"].append({"path": f"/api/agents/runs/53/{suffix}", "status": response.status, "top_level_keys": list(data)})
                assert response.status == 200
            approval = read_detail(context, token, "before_navigation")
            page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
            expect(page.get_by_role("heading", name="Agent 测试生成", exact=True)).to_be_visible(timeout=30000)
            expect(page.locator(".agent-node-row")).to_have_count(1, timeout=30000)
            expect(page.get_by_role("button", name="确认规则", exact=True)).to_be_visible(timeout=30000)
            expect(page.locator(".agent-source-card")).to_contain_text("同步优学", timeout=30000)
            expect(page.locator(".agent-node-row")).to_contain_text("等待确认", timeout=30000)
            REPORT["steps"].append({"name": "初次打开暂停页面", "node": page.locator(".agent-node-row").inner_text()})
            screenshot(page, "paused-before-document.png")
            page.get_by_role("button", name="知识库管理", exact=True).click()
            expect(page.get_by_placeholder("搜索文件名...")).to_be_visible(timeout=30000)
            with page.expect_response(lambda response: "/api/knowledge-list?" in response.url and "search=%E5%90%8C%E6%AD%A5%E4%BC%98%E5%AD%A6" in response.url, timeout=30000) as searched:
                page.get_by_placeholder("搜索文件名...").fill("同步优学")
            found = searched.value.json()["documents"]
            index = next(i for i, item in enumerate(found) if item["id"] == 287)
            card = page.locator(".knowledge-base-shell .doc-card").nth(index)
            expect(card).to_contain_text("同步优学 · 产品需求文档.pdf", timeout=30000)
            card.get_by_role("button", name="查看", exact=True).click()
            doc = page.get_by_role("dialog")
            expect(doc).to_contain_text("同步优学", timeout=30000)
            screenshot(page, "source-document-preview.png")
            REPORT["steps"].append({"name": "查看真实doc287原文", "document_ids": [item["id"] for item in found], "title": doc.locator(".modal-title").inner_text()})
            doc.get_by_role("button", name="关闭", exact=True).click()
            expect(doc).not_to_be_visible(timeout=15000)
            page.get_by_role("button", name="Agent 测试生成", exact=True).click()
            expect(page.get_by_role("button", name="确认规则", exact=True)).to_be_visible(timeout=30000)
            expect(page.locator(".agent-node-row")).to_contain_text("等待确认", timeout=30000)
            screenshot(page, "paused-after-document.png")
            after_document = read_detail(context, token, "after_document_return")
            assert after_document == approval
            before_questions = inspect_questions(page, after_document, "after-document")
            page.reload(wait_until="domcontentloaded")
            expect(page.get_by_role("button", name="确认规则", exact=True)).to_be_visible(timeout=30000)
            expect(page.locator(".agent-node-row")).to_contain_text("等待确认", timeout=30000)
            screenshot(page, "paused-after-reload.png")
            after_reload = read_detail(context, token, "after_reload")
            assert after_reload == approval
            after_questions = inspect_questions(page, after_reload, "after-reload")
            assert before_questions == after_questions
            REPORT["same_approval_and_questions_restored"] = True
            assert not REPORT["business_write_requests"] and not REPORT["http_errors"] and not REPORT["page_errors"]
            REPORT["status"] = "passed"
        except Exception as error:
            REPORT["status"] = "failed"
            REPORT["error"] = {"type": type(error).__name__, "message": str(error)[:2500]}
            REPORT["body_excerpt"] = page.locator("body").inner_text()[:2500]
            screenshot(page, "probe-failure.png")
            raise
        finally:
            REPORT["finished_at"] = datetime.now(timezone.utc).isoformat()
            (OUTPUT / "ui-paused-readonly-report.json").write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
            context.close()
            browser.close()

if __name__ == "__main__": main()
