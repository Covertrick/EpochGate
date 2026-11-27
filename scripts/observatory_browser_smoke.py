"""Exercise an empty local Observatory through its real browser and API."""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765/")
    parser.add_argument("--screenshots", type=Path)
    parser.add_argument("--executable", help="Use an installed Chromium or Edge browser")
    args = parser.parse_args()
    if args.screenshots:
        args.screenshots.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.executable)
        try:
            smoke(browser, args.base_url, args.screenshots)
        finally:
            browser.close()


def smoke(browser, base_url: str, screenshots: Path | None) -> None:
    errors = []

    def capture(page, name: str) -> None:
        if screenshots:
            page.screenshot(style="#notice { visibility: hidden; }", path=screenshots / name)

    page = browser.new_page(viewport={"width":1920,"height":1080}, accept_downloads=True)
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    page.goto(base_url)
    expect(page.locator("#recent")).to_contain_text("暂无记录")
    scenes = ["stale-write","idempotency-conflict","crash-window","cancellation"]
    for scenario in scenes:
        page.locator(f'[data-demo="{scenario}"]').click()
        expect(page.locator("#run-heading")).to_be_visible(timeout=25000)
        expect(page.get_by_role("heading", name="Admission · 执行资格")).to_be_visible()
        if scenario == "stale-write":
            expect(page.locator("#run-metrics")).to_contain_text("VERIFIED")
            page.get_by_role("tab", name="日志").click()
            page.get_by_role("searchbox").fill("impossible-match")
            expect(page.get_by_text("暂无匹配输出。")).to_be_visible()
            page.get_by_role("tab", name="时间线").click()
            page.get_by_role("button", name="旧 token 不能发布副作用").first.click()
            expect(page.locator("dialog")).to_be_visible()
            page.get_by_text("原始事件 JSON").click()
            expect(page.locator("dialog pre")).to_contain_text("result_withheld")
            page.get_by_role("button",name="关闭").click()
            capture(page, "run-detail.png")
            page.get_by_role("tab", name="证据").click()
            page.locator("[data-attempt]").first.click()
            expect(page.locator("dialog")).to_contain_text("Fencing Token")
            page.get_by_role("button", name="关闭").click()
            page.get_by_role("tab", name="原始数据").click()
            with page.expect_download() as dl:
                page.get_by_role("button", name="下载 JSON").click()
            assert dl.value.suggested_filename.endswith(".json")
            page.reload()
            expect(page.get_by_role("tab", name="原始数据")).to_have_attribute("aria-selected","true")
        if scenario == "crash-window":
            expect(page.locator("#run-metrics")).to_contain_text("VERIFIED")
            expect(page.get_by_role("heading", name="Recovery · 恢复决定")).to_be_visible()
        if scenario == "cancellation":
            expect(page.get_by_text("观测到执行槽位释放")).to_be_visible()
        page.get_by_role("link", name="总览", exact=True).click()
        expect(page.locator("#metrics")).not_to_contain_text("—")
    capture(page, "overview.png")
    page.get_by_role("link", name="运行记录", exact=True).click()
    page.get_by_role("searchbox").fill("token-01")
    expect(page.locator("#run-table tbody tr")).to_have_count(1)
    page.get_by_role("searchbox").fill("")
    page.locator("[data-select-run]").nth(0).check()
    page.locator("[data-select-run]").nth(1).check()
    page.get_by_role("button", name="对比所选运行").click()
    expect(page.locator("dialog")).to_contain_text("不同事件计数")
    page.get_by_role("button", name="关闭").click()
    expect(page.locator("#run-table tbody tr")).to_have_count(4)
    capture(page, "runs.png")
    page.get_by_role("link", name="运行工作台", exact=True).click()
    page.get_by_role("button", name="使用问候样本").click(timeout=20000)
    expect(page.get_by_text("文件树 · 2")).to_be_visible()
    page.get_by_role("button", name="继续定义任务").click()
    expect(page.get_by_label("任务描述")).to_have_value("把 note.py 里的 greet 改成返回 hello, 加上名字。只改 note.py。")
    page.get_by_role("button", name="继续预检").click()
    expect(page.locator("#policy").get_by_text("Fencing Token")).to_be_visible(timeout=25000)
    capture(page, "workspace.png")
    page.get_by_role("link", name="实验分析", exact=True).click()
    expect(page.get_by_text("27/27")).to_be_visible(timeout=20000)
    expect(page.locator("#cell-table tbody tr")).to_have_count(27)
    page.locator("#cell-result").select_option("violation")
    expect(page.locator("#cell-table tbody tr")).to_have_count(4)
    page.get_by_role("button", name="sqlite-inventory-check_commit_gap", exact=True).click()
    ledger = page.locator("dialog details").filter(has=page.get_by_text("目标端账本", exact=True))
    ledger.locator("summary").click()
    expect(ledger.locator("pre")).to_contain_text("target_effects")
    with page.expect_download() as dl:
        page.get_by_role("button", name="target.sqlite", exact=True).click()
    assert dl.value.suggested_filename == "target.sqlite"
    page.get_by_role("button", name="关闭").click()
    page.get_by_role("button", name="从目标账本独立重算").click()
    expect(page.locator("#audit-result")).to_contain_text("VERIFIED", timeout=25000)
    with page.expect_download() as export:
        page.get_by_role("button", name="导出 CSV", exact=True).click()
    assert export.value.suggested_filename == "effect-history.csv"
    capture(page, "research.png")
    page.get_by_role("link", name="Evidence Audit", exact=True).click()
    expect(page.get_by_role("heading", name="Evidence Audit")).to_be_visible()
    expect(page.get_by_text("复核记录", exact=True)).to_be_visible()
    capture(page,"audit.png")
    page.get_by_role("link", name="系统状态", exact=True).click()
    expect(page.get_by_role("heading", name="组件状态")).to_be_visible(timeout=25000)
    page.get_by_role("button", name="SQLite", exact=True).click()
    expect(page.locator("dialog")).to_contain_text("CONNECTED")
    page.get_by_role("button", name="关闭").click()
    expect(page.locator("#system-log pre")).to_be_visible()
    capture(page, "system.png")
    page.keyboard.press("Control+k")
    page.get_by_role("searchbox", name="运行或证据 ID").fill("token-01")
    page.locator("dialog").get_by_role("button", name="搜索").click()
    expect(page.locator("dialog")).to_contain_text("token-01")
    page.keyboard.press("Escape")
    page.get_by_role("link", name="总览", exact=True).click()
    expect(page.locator("#metrics")).not_to_contain_text("—")
    for width, height in [(1920, 1080), (1440, 900), (1366, 768), (1024, 768), (390, 844)]:
        page.set_viewport_size({"width": width, "height": height})
        assert page.evaluate("document.documentElement.scrollWidth === innerWidth"), width
    capture(page, "overview-mobile.png")
    page.context.set_offline(True)
    expect(page.locator("#connection")).to_have_attribute("data-offline","")
    page.context.set_offline(False)
    expect(page.locator("#metrics")).not_to_contain_text("—")
    expect(page.locator("#connection")).not_to_have_attribute("data-offline","")
    assert not errors, errors
    print("Four live demos, run evidence and export, history filter, workspace preflight, research downloads and audit, system, responsive layout, console all passed.")


if __name__ == "__main__":
    main()
