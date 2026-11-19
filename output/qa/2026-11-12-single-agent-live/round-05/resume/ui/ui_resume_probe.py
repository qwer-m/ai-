"""真实运行页面与文档往返只读验收；不触发生成、审批或任务控制。"""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / "backend"))

from core.authn.auth import create_access_token
from core.db.database import SessionLocal
from core.db.model_defs import User
from playwright.sync_api import sync_playwright, expect


PHASE = "resume"

RUN_ID = 53
OUTPUT = Path(__file__).resolve().parent
OUTPUT.mkdir(parents=True, exist_ok=True)
REPORT = {"run_id": RUN_ID, "project_id": 8, "document_id": 287,
          "probe_phase": PHASE, "recovery_proof_claimed": False,
          "started_at": datetime.now(timezone.utc).isoformat(),
          "console_errors": [], "page_errors": [], "http_errors": [],
          "request_failures": [], "business_write_requests": [], "api_checks": [], "steps": [], "screenshots": [], "existing_user_browser_observed": False}


def save():
    (OUTPUT / "ui-readonly-report.json").write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")


def screenshot(page, name):
    path = OUTPUT / name
    page.screenshot(path=str(path), full_page=True)
    REPORT["screenshots"].append(str(path))


def main():
    with SessionLocal() as db:
        user = db.get(User, 1)
        assert user is not None
        token = create_access_token({"sub": user.username}, expires_delta=timedelta(minutes=30))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 1100}, locale="zh-CN")
        context.add_init_script("""({token}) => {
            localStorage.setItem('token', token);
            localStorage.setItem('currentProjectId', '8');
            localStorage.setItem('currentActiveTab', 'agent-generation');
        }""".replace("({token}) =>", "(() =>").replace("setItem('token', token)", "setItem('token', " + json.dumps(token) + ")") + ")()")
        page = context.new_page()
        page.on("console", lambda msg: REPORT["console_errors"].append({"text": msg.text, "location": msg.location}) if msg.type == "error" else None)
        page.on("pageerror", lambda error: REPORT["page_errors"].append(str(error)))
        page.on("requestfailed", lambda request: REPORT["request_failures"].append({"method": request.method, "url": request.url, "failure": request.failure}))
        page.on("response", lambda response: REPORT["http_errors"].append({"url": response.url, "status": response.status}) if response.status >= 400 else None)
        page.on("request", lambda request: REPORT["business_write_requests"].append({"method": request.method, "url": request.url}) if request.method not in {"GET", "HEAD", "OPTIONS"} else None)
        try:
            page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
            expect(page.get_by_role("heading", name="Agent 测试生成", exact=True)).to_be_visible(timeout=30000)
            expect(page.get_by_label("用例数量", exact=True)).to_have_value("70", timeout=30000)
            expect(page.locator(".agent-source-card")).to_contain_text("同步优学", timeout=30000)
            expect(page.locator(".agent-node-row")).to_have_count(1, timeout=30000)
            expect(page.get_by_role("dialog", name="确认需求规则", exact=True)).not_to_be_visible(timeout=30000)
            expect(page.get_by_role("button", name="确认规则", exact=True)).to_have_count(0, timeout=30000)
            REPORT["steps"].append({"name": "初次打开恢复后的生成页面", "single_main_node": True, "pending_conflict_approval_entry": False, "approval_dialog_visible": False,
                                    "node_label": page.locator(".agent-node-row").inner_text()})
            screenshot(page, "ui-agent-before-document.png")
            for suffix in ("summary", "metrics", "events"):
                response = context.request.get(f"http://127.0.0.1:8000/api/agents/runs/{RUN_ID}/{suffix}", headers={"Authorization": f"Bearer {token}"})
                data = response.json()
                REPORT["api_checks"].append({"path": f"/api/agents/runs/{RUN_ID}/{suffix}", "status": response.status,
                                             "top_level_keys": list(data) if isinstance(data, dict) else "list"})
                assert response.ok
            response = context.request.get(f"http://127.0.0.1:8000/api/agents/runs/{RUN_ID}", headers={"Authorization": f"Bearer {token}"})
            payload = response.json()
            run = payload.get("run", payload)
            REPORT["api_checks"].append({"path": f"/api/agents/runs/{RUN_ID}", "status": response.status,
                                         "run_id": run.get("id"), "run_status": run.get("status"),
                                         "run_error_message": run.get("error_message"),
                                         "usage": run.get("run_context", {}).get("usage", {}),
                                         "approvals": [{key: approval.get(key) for key in ("id", "status", "decided_at")} for approval in run.get("approvals", [])],
                                         "nodes": [{key: node.get(key) for key in ("id", "node_key", "status", "attempt")} for node in run.get("nodes", [])]})
            assert response.ok and run["id"] == RUN_ID
            assert next(a for a in run["approvals"] if a["id"] == 13)["status"] == "approved"
            assert run["status"] in {"running", "completed"}
            assert run["run_context"]["execution_contract"]["workflow"]["definition"]["execution_mode"] == "agent_network"
            page.get_by_role("button", name="知识库管理", exact=True).click()
            expect(page.locator(".knowledge-base-shell")).to_be_visible(timeout=30000)
            REPORT["steps"].append({"name": "切换知识库", "body_excerpt": page.locator("main").inner_text()[:300] if page.locator("main").count() else page.locator(".knowledge-base-shell").inner_text()[:300]})
            with page.expect_response(lambda response: "/api/knowledge-list?" in response.url and "search=%E5%90%8C%E6%AD%A5%E4%BC%98%E5%AD%A6" in response.url, timeout=30000) as search_response:
                page.get_by_placeholder("搜索文件名...").fill("同步优学")
            found = search_response.value.json()["documents"]
            REPORT["steps"].append({"name": "按真实文件名查询", "status": search_response.value.status,
                                    "document_ids": [item["id"] for item in found]})
            index = next(index for index, item in enumerate(found) if item["id"] == 287)
            item = page.locator(".knowledge-base-shell .doc-card").nth(index)
            expect(item).to_contain_text("同步优学 · 产品需求文档.pdf", timeout=30000)
            item.get_by_role("button", name="查看", exact=True).click()
            dialog = page.get_by_role("dialog")
            expect(dialog).to_be_visible(timeout=30000)
            expect(dialog).to_contain_text("同步优学", timeout=30000)
            screenshot(page, "ui-source-document-preview.png")
            REPORT["steps"].append({"name": "查看真实需求文档", "dialog_title": dialog.locator(".modal-title").inner_text()})
            dialog.get_by_role("button", name="关闭", exact=True).click()
            expect(dialog).not_to_be_visible(timeout=15000)
            page.get_by_role("button", name="Agent 测试生成", exact=True).click()
            expect(page.get_by_role("heading", name="Agent 测试生成", exact=True)).to_be_visible(timeout=30000)
            expect(page.get_by_label("用例数量", exact=True)).to_have_value("70", timeout=30000)
            expect(page.locator(".agent-node-row")).to_have_count(1, timeout=30000)
            screenshot(page, "ui-agent-after-document.png")
            expect(page.get_by_role("dialog", name="确认需求规则", exact=True)).not_to_be_visible(timeout=30000)
            expect(page.get_by_role("button", name="确认规则", exact=True)).to_have_count(0, timeout=30000)
            REPORT["steps"].append({"name": "查看文档后返回生成页面", "single_main_node": True,
                                    "node_label": page.locator(".agent-node-row").inner_text(), "approval_dialog_visible": False})
            page.reload(wait_until="domcontentloaded")
            expect(page.get_by_label("用例数量", exact=True)).to_have_value("70", timeout=30000)
            expect(page.locator(".agent-node-row")).to_have_count(1, timeout=30000)
            screenshot(page, "ui-agent-after-reload.png")
            expect(page.get_by_role("dialog", name="确认需求规则", exact=True)).not_to_be_visible(timeout=30000)
            expect(page.get_by_role("button", name="确认规则", exact=True)).to_have_count(0, timeout=30000)
            REPORT["steps"].append({"name": "刷新生成页面", "single_main_node": True,
                                    "node_label": page.locator(".agent-node-row").inner_text(), "approval_dialog_visible": False})
            assert not REPORT["business_write_requests"]
            REPORT["status"] = "passed"
        except Exception as error:
            REPORT["status"] = "failed"
            REPORT["error"] = {"type": type(error).__name__, "message": str(error)[:2500]}
            screenshot(page, "ui-probe-failure.png")
            REPORT["failure_body_excerpt"] = page.locator("body").inner_text()[:2200]
            raise
        finally:
            REPORT["finished_at"] = datetime.now(timezone.utc).isoformat()
            save()
            context.close()
            browser.close()


if __name__ == "__main__":
    main()
