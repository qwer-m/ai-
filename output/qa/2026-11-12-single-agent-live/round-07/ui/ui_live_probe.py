"""Run55真实只读页面验收，不拦截网络、不触发业务写入。"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlparse
ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / "backend"))
from core.authn.auth import create_access_token
from core.db.database import SessionLocal
from core.db.model_defs import User
from playwright.sync_api import sync_playwright, expect

OUTPUT = Path(__file__).resolve().parent
REPORT = {"run_id": 55, "project_id": 8, "document_id": 287, "started_at": datetime.now(timezone.utc).isoformat(),
    "console_errors": [], "page_errors": [], "http_errors": [], "request_failures": [], "business_write_requests": [],
    "api_checks": [], "ui_run_reads": [], "steps": [], "screenshots": [], "network_interception": False,
    "explicit_full_detail_reads": 0, "existing_user_browser_observed": False}

def screenshot(page, name):
    path = OUTPUT / name
    page.screenshot(path=str(path), full_page=True)
    REPORT["screenshots"].append(str(path))

def observed_response(response):
    if response.status >= 400:
        REPORT["http_errors"].append({"url": response.url, "status": response.status})
    if urlparse(response.url).path == "/api/agents/runs/55" and response.status == 200:
        run = response.json()["run"]
        REPORT["ui_run_reads"].append({"observed_at": datetime.now(timezone.utc).isoformat(), "run_id": run["id"], "status": run["status"],
            "usage": run.get("run_context", {}).get("usage", {}), "error_message": run.get("error_message", ""),
            "nodes": [{k: n.get(k) for k in ("id", "node_key", "attempt", "status")} for n in run.get("nodes", [])],
            "approvals": [{k: a.get(k) for k in ("id", "status")} for a in run.get("approvals", [])],
            "persisted_case_count": len((run.get("test_generation_result") or {}).get("test_cases", []))})

def snapshot_page(page, name):
    expect(page.locator(".agent-node-row")).to_have_count(1, timeout=30000)
    expect(page.get_by_role("dialog", name="确认需求规则", exact=True)).not_to_be_visible(timeout=30000)
    expect(page.get_by_role("button", name="确认规则", exact=True)).to_have_count(0, timeout=30000)
    row = page.locator(".agent-node-row").inner_text()
    events = page.locator(".agent-event-trace li").all_text_contents()
    REPORT["steps"].append({"name": name, "single_main_node": True, "node_label": row,
        "pending_conflict_entry": False, "approval_dialog_visible": False, "visible_events": events})
    screenshot(page, name + ".png")

def summary(context, token, phase):
    response = context.request.get("http://127.0.0.1:8000/api/agents/runs/55/summary", headers={"Authorization": "Bearer " + token}, timeout=30000)
    run = response.json()["run"]
    REPORT["api_checks"].append({"phase": phase, "path": "/api/agents/runs/55/summary", "status": response.status,
        "run_id": run["id"], "run_status": run["status"], "run_attempt": run["run_attempt"], "error_message": run.get("error_message", "")})
    assert response.status == 200 and run["id"] == 55
    return run

def main():
    with SessionLocal() as db:
        token = create_access_token({"sub": db.get(User, 1).username}, expires_delta=timedelta(minutes=15))
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 1100}, locale="zh-CN")
        context.add_init_script("(() => { localStorage.setItem('token', " + json.dumps(token) + ");localStorage.setItem('currentProjectId', '8');localStorage.setItem('currentActiveTab', 'agent-generation'); })()")
        page = context.new_page()
        page.on("console", lambda msg: REPORT["console_errors"].append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda error: REPORT["page_errors"].append(str(error)))
        page.on("requestfailed", lambda request: REPORT["request_failures"].append({"url": request.url, "failure": request.failure}))
        page.on("request", lambda request: REPORT["business_write_requests"].append({"method": request.method, "url": request.url}) if request.method not in {"GET", "HEAD", "OPTIONS"} else None)
        page.on("response", observed_response)
        try:
            before = summary(context, token, "before")
            page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
            expect(page.get_by_role("heading", name="Agent 测试生成", exact=True)).to_be_visible(timeout=30000)
            expect(page.get_by_label("用例数量", exact=True)).to_have_value("70", timeout=30000)
            expect(page.locator(".agent-source-card")).to_contain_text("同步优学", timeout=30000)
            snapshot_page(page, "agent-before-document")
            page.get_by_role("button", name="知识库管理", exact=True).click()
            expect(page.get_by_placeholder("搜索文件名...")).to_be_visible(timeout=30000)
            with page.expect_response(lambda response: "/api/knowledge-list?" in response.url and "search=%E5%90%8C%E6%AD%A5%E4%BC%98%E5%AD%A6" in response.url, timeout=30000) as searched:
                page.get_by_placeholder("搜索文件名...").fill("同步优学")
            found = searched.value.json()["documents"]
            index = next(i for i, item in enumerate(found) if item["id"] == 287)
            card = page.locator(".knowledge-base-shell .doc-card").nth(index)
            expect(card).to_contain_text("同步优学 · 产品需求文档.pdf", timeout=30000)
            card.get_by_role("button", name="查看", exact=True).click()
            dialog = page.get_by_role("dialog")
            expect(dialog).to_contain_text("同步优学", timeout=30000)
            REPORT["steps"].append({"name": "查看真实doc287", "document_ids": [d["id"] for d in found], "title": dialog.locator(".modal-title").inner_text()})
            screenshot(page, "source-document-preview.png")
            dialog.get_by_role("button", name="关闭", exact=True).click()
            expect(dialog).not_to_be_visible(timeout=15000)
            page.get_by_role("button", name="Agent 测试生成", exact=True).click()
            expect(page.get_by_role("heading", name="Agent 测试生成", exact=True)).to_be_visible(timeout=30000)
            snapshot_page(page, "agent-after-document")
            page.reload(wait_until="domcontentloaded")
            expect(page.get_by_label("用例数量", exact=True)).to_have_value("70", timeout=30000)
            snapshot_page(page, "agent-after-reload")
            after = summary(context, token, "after_reload")
            seqs = page.locator(".agent-event-sequence").all_text_contents()
            maxseq = max((int(s.lstrip("#")) for s in seqs), default=0)
            path = f"/api/agents/runs/55/events?after_sequence={max(0, maxseq - 12)}&limit=20"
            response = context.request.get("http://127.0.0.1:8000" + path, headers={"Authorization": "Bearer " + token}, timeout=30000)
            events = response.json()
            REPORT["api_checks"].append({"path": path, "status": response.status, "items": [{k: e.get(k) for k in ("run_id", "node_run_id", "sequence", "event_type")} for e in events["items"]], "visible_latest_sequence": maxseq})
            assert response.status == 200 and all(e["run_id"] == 55 for e in events["items"])
            assert not maxseq or any(e["sequence"] == maxseq for e in events["items"])
            assert REPORT["ui_run_reads"] and all(r["run_id"] == 55 for r in REPORT["ui_run_reads"])
            latest = REPORT["ui_run_reads"][-1]
            REPORT["final_run_status"] = after["status"]
            REPORT["latest_ui_run"] = latest
            if latest["persisted_case_count"] > 0:
                response = context.request.get("http://127.0.0.1:8000/api/agents/runs/55/test-cases.xlsx", headers={"Authorization": "Bearer " + token}, timeout=30000)
                body = response.body()
                REPORT["export"] = {"status": response.status, "persisted_case_count": latest["persisted_case_count"], "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
                assert response.status == 200
                (OUTPUT / "run55-real-test-cases.xlsx").write_bytes(body)
            else:
                REPORT["export"] = {"status": "not_requested", "reason": "当前持久化真实用例为0，不请求导出"}
            assert not REPORT["business_write_requests"] and not REPORT["http_errors"] and not REPORT["page_errors"]
            REPORT["status"] = "passed"
        except Exception as error:
            REPORT["status"] = "failed"
            REPORT["error"] = {"type": type(error).__name__, "message": str(error)[:2500]}
            screenshot(page, "probe-failure.png")
            REPORT["body_excerpt"] = page.locator("body").inner_text()[:2400]
            raise
        finally:
            REPORT["finished_at"] = datetime.now(timezone.utc).isoformat()
            (OUTPUT / "ui-readonly-report.json").write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
            context.close()
            browser.close()

if __name__ == "__main__": main()
