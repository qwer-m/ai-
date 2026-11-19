"""以真实请求顺序验证知识库搜索，不拦截网络或伪造数据。"""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import time
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / "backend"))
from core.authn.auth import create_access_token
from core.db.database import SessionLocal
from core.db.model_defs import User
from playwright.sync_api import sync_playwright, expect


def main():
    phase = sys.argv[1]
    with SessionLocal() as db:
        token = create_access_token({"sub": db.get(User, 1).username}, expires_delta=timedelta(minutes=10))
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            for trial in range(3):
                context = browser.new_context(viewport={"width": 1600, "height": 1100}, locale="zh-CN")
                context.add_init_script("(() => { localStorage.setItem('token', " + json.dumps(token) + ");localStorage.setItem('currentProjectId', '8');localStorage.setItem('currentActiveTab', 'agent-generation'); })()")
                page = context.new_page()
                result = {"trial": trial + 1, "requests": [], "responses": [], "console_errors": [], "page_errors": []}
                pending = set()
                def request_started(request):
                    if "/api/knowledge-list?" in request.url:
                        pending.add(request)
                        result["requests"].append({"url": request.url, "observed_at": datetime.now(timezone.utc).isoformat()})
                def response_received(response):
                    if "/api/knowledge-list?" in response.url:
                        data = response.json()
                        result["responses"].append({"url": response.url, "status": response.status,
                            "observed_at": datetime.now(timezone.utc).isoformat(),
                            "search": parse_qs(urlparse(response.url).query).get("search", [""])[0],
                            "document_ids": [item["id"] for item in data.get("documents", [])],
                            "total": data.get("pagination", {}).get("total"), "timing": response.request.timing})
                page.on("request", request_started)
                page.on("response", response_received)
                page.on("requestfinished", lambda request: pending.discard(request))
                page.on("requestfailed", lambda request: pending.discard(request))
                page.on("console", lambda msg: result["console_errors"].append(msg.text) if msg.type == "error" else None)
                page.on("pageerror", lambda error: result["page_errors"].append(str(error)))
                page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                expect(page.get_by_role("heading", name="Agent 测试生成", exact=True)).to_be_visible(timeout=30000)
                page.get_by_role("button", name="知识库管理", exact=True).click()
                search = page.get_by_placeholder("搜索文件名...")
                expect(search).to_be_visible(timeout=30000)
                search.fill("同步优学")
                try:
                    expect(page.locator(".knowledge-doc-title")).to_have_count(2, timeout=4000)
                    expect(page.locator(".knowledge-doc-title").first).to_have_text("同步优学 · 产品需求文档.pdf", timeout=4000)
                    result["search_result_matches"] = True
                except AssertionError:
                    result["search_result_matches"] = False
                deadline = time.monotonic() + 8
                while pending and time.monotonic() < deadline:
                    page.wait_for_timeout(30)
                result["search_value"] = search.input_value()
                result["visible_titles"] = page.locator(".knowledge-doc-title").all_text_contents()
                result["visible_total"] = page.locator(".knowledge-base-shell").inner_text()[-250:]
                result["search_result_matches"] = (len(result["visible_titles"]) == 2 and
                    all(title == "同步优学 · 产品需求文档.pdf" for title in result["visible_titles"]))
                results.append(result)
                if phase == "after-fix" and trial == 2:
                    page.locator(".dashboard-project-select").select_option("9")
                    expect(page.get_by_placeholder("搜索文件名...")).to_have_value("", timeout=30000)
                    page.get_by_placeholder("搜索文件名...").fill("机构管理")
                    expect(page.locator(".knowledge-doc-title")).to_have_count(1, timeout=30000)
                    result["project_9"] = {"query": page.get_by_placeholder("搜索文件名...").input_value(),
                                            "titles": page.locator(".knowledge-doc-title").all_text_contents()}
                    page.locator(".dashboard-project-select").select_option("8")
                    expect(page.get_by_placeholder("搜索文件名...")).to_have_value("同步优学", timeout=30000)
                    expect(page.locator(".knowledge-doc-title")).to_have_count(2, timeout=30000)
                    result["project_8_restored"] = {"query": page.get_by_placeholder("搜索文件名...").input_value(),
                                                     "titles": page.locator(".knowledge-doc-title").all_text_contents()}
                    page.locator(".dashboard-project-select").select_option("9")
                    expect(page.get_by_placeholder("搜索文件名...")).to_have_value("机构管理", timeout=30000)
                    result["project_9_restored_query"] = page.get_by_placeholder("搜索文件名...").input_value()
                    page.locator(".dashboard-project-select").select_option("8")
                    expect(page.get_by_placeholder("搜索文件名...")).to_have_value("同步优学", timeout=30000)
                    expect(page.locator(".knowledge-doc-title")).to_have_count(2, timeout=30000)
                    result["rapid_project_return_preserved"] = True
                context.close()
        finally:
            output = Path(__file__).resolve().parent / f"knowledge-search-{phase}.json"
            if 'page' in locals() and not page.is_closed():
                result["last_project"] = page.locator(".dashboard-project-select").input_value()
                result["last_query"] = page.get_by_placeholder("搜索文件名...").input_value()
                result["last_titles"] = page.locator(".knowledge-doc-title").all_text_contents()
                result["last_storage"] = page.evaluate("Object.fromEntries(Object.entries(sessionStorage).filter(([k])=>k.startsWith('kb_')))")
                page.screenshot(path=str(Path(__file__).resolve().parent / f"knowledge-search-{phase}-last.png"), full_page=True)
            output.write_text(json.dumps({"phase": phase, "scope": "knowledge_base_not_run_specific", "project_id": 8,
                "network_interception": False, "business_mutations": False, "trials": results}, ensure_ascii=False, indent=2), encoding="utf-8")
            browser.close()
    output = Path(__file__).resolve().parent / f"knowledge-search-{phase}.json"
    output.write_text(json.dumps({"phase": phase, "scope": "knowledge_base_not_run_specific", "project_id": 8,
        "network_interception": False, "business_mutations": False, "trials": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "matches": [result["search_result_matches"] for result in results]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
