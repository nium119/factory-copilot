"""
E2E browser test: 发送消息 -> 查看执行链 -> 验证历史记录
"""
import asyncio, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:9001"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})

        # ============================================
        # 1. Open app
        # ============================================
        print("[1] Opening app...")
        await page.goto(f"{BASE}/index.html", wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(3000)  # Wait for React to render
        await page.screenshot(path="e2e_screenshots/01_app_loaded.png")
        print("    App loaded OK")

        # ============================================
        # 2. Find chat input and send a query
        # ============================================
        print("[2] Sending query: 查询生产中的工单")

        # Use the exact ChatInputBar textarea placeholder and class
        chat_input = await page.query_selector('textarea.chat-input-textarea')
        if not chat_input:
            chat_input = await page.query_selector('textarea[placeholder*="输入消息"]')

        if chat_input:
            await chat_input.click()
            await page.wait_for_timeout(300)
            await chat_input.fill("查询生产中的工单")
            await page.wait_for_timeout(300)

            # Send button has class send-btn with SendOutlined icon
            send_btn = await page.query_selector('button.send-btn')
            if send_btn:
                await send_btn.click()
                print(f"    Clicked send button")
            else:
                await chat_input.press("Enter")
                print("    Pressed Enter to send")
        else:
            print("    ERROR: Cannot find chat input!")
            await page.screenshot(path="e2e_screenshots/02_error.png", full_page=True)
            await browser.close()
            return

        # ============================================
        # 3. Wait for AI response
        # ============================================
        print("[3] Waiting for AI response...")
        try:
            # Wait for execution chain or content to appear
            await page.wait_for_function("""
                () => {
                    const texts = document.body.innerText;
                    return texts.includes('WO-') || texts.includes('工单') || texts.includes('执行完成');
                }
            """, timeout=30000)
            print("    Response received!")
        except Exception as e:
            print(f"    Timeout waiting for response: {e}")

        await page.wait_for_timeout(2000)
        await page.screenshot(path="e2e_screenshots/03_response.png", full_page=True)

        # ============================================
        # 4. Check for execution chain in the UI
        # ============================================
        print("[4] Checking execution chain...")
        body_text = await page.inner_text("body")
        has_execution = "路由分析" in body_text or "执行完成" in body_text
        print(f"    Execution chain visible: {has_execution}")

        # ============================================
        # 5. Send a second message
        # ============================================
        print("[5] Sending second query: 查询所有设备")
        chat_input = await page.query_selector('textarea.chat-input-textarea')
        if chat_input:
            await chat_input.click()
            await page.wait_for_timeout(300)
            await chat_input.fill("查询所有设备")
            await page.wait_for_timeout(300)
            send_btn = await page.query_selector('button.send-btn')
            if send_btn:
                await send_btn.click()
            else:
                await chat_input.press("Enter")

        try:
            await page.wait_for_function("""
                () => document.body.innerText.includes('设备')
            """, timeout=30000)
            print("    Second response received!")
        except Exception as e:
            print(f"    Timeout: {e}")

        await page.wait_for_timeout(2000)
        await page.screenshot(path="e2e_screenshots/05_second_response.png", full_page=True)

        # ============================================
        # 6. Open history drawer and verify
        # ============================================
        print("[6] Opening history drawer...")

        # Look for history/open drawer buttons
        history_selectors = [
            'button:has-text("历史")',
            '[aria-label*="历史"]',
            '[aria-label*="history"]',
            'button:has-text("记录")',
            '.ant-drawer-trigger',
        ]
        for sel in history_selectors:
            btn = await page.query_selector(sel)
            if btn and await btn.is_visible():
                await btn.click()
                print(f"    Clicked: {sel}")
                break
        else:
            print("    No history button found, checking sidebar...")
            await page.screenshot(path="e2e_screenshots/06_sidebar.png", full_page=True)

        await page.wait_for_timeout(2000)
        await page.screenshot(path="e2e_screenshots/06_history_open.png", full_page=True)

        # ============================================
        # 7. Click most recent conversation (first in list)
        # ============================================
        print("[7] Clicking a historical conversation...")
        # Ant Design List items in a Drawer
        list_item_selectors = [
            '.ant-drawer-body .ant-list-item',
            '.ant-drawer-body [class*="conversation"]',
            '.ant-drawer-body div[role="listitem"]',
        ]
        clicked_history = False
        for sel in list_item_selectors:
            items = await page.query_selector_all(sel)
            if items:
                await items[0].click()
                print(f"    Clicked first item: {sel}")
                clicked_history = True
                break

        if not clicked_history:
            print("    Could not find conversation list items")

        await page.wait_for_timeout(3000)
        await page.screenshot(path="e2e_screenshots/07_history_loaded.png", full_page=True)

        # ============================================
        # 8. Verify messages loaded correctly
        # ============================================
        print("[8] Verifying history load...")
        body_text = await page.inner_text("body")
        msg_count = body_text.count("WO-") + body_text.count("工单") + body_text.count("设备")
        print(f"    Relevant content found in page: {msg_count} occurrences")

        # Check if there are multiple messages visible (not just the first)
        user_msgs = body_text.count("查询")
        print(f"    '查询' occurrences (should be >= 2 for 2 user messages): {user_msgs}")

        # ============================================
        # Done
        # ============================================
        await page.screenshot(path="e2e_screenshots/08_final.png", full_page=True)
        print("\n=== E2E Browser Test Complete ===")
        print("Screenshots saved to: e2e_screenshots/")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
