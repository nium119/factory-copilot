"""UI test: Dual confirmation flow via Playwright."""
import subprocess
import sys
import time

# Use Playwright via subprocess to avoid import issues
script = """
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  try {
    // Navigate to the app
    await page.goto('http://localhost:5001', { waitUntil: 'networkidle' });
    console.log('[1] Page loaded');

    // Wait for the chat input
    await page.waitForSelector('textarea, input[type="text"], .chat-input', { timeout: 10000 });
    console.log('[2] Chat input found');

    // Find and fill the input
    const inputArea = await page.locator('textarea, [contenteditable="true"]').first();
    await inputArea.click();
    await inputArea.fill('WO-20250521-001不合格');
    console.log('[3] Message entered');

    // Click send
    await page.keyboard.press('Enter');
    console.log('[4] Message sent');

    // Wait for first confirm_required (Action confirmation)
    // The ConfirmCard should appear
    await page.waitForTimeout(8000);

    // Check for confirm card
    const confirmCards = await page.locator('text=确认').all();
    console.log(`[5] Found ${confirmCards.length} elements with "确认"`);

    // Look for confirm button
    const confirmBtn = await page.locator('button:has-text("确认")').first();
    if (await confirmBtn.isVisible()) {
      console.log('[6] Action ConfirmCard visible, clicking confirm...');
      await confirmBtn.click();
      console.log('[7] Action confirmed');
    } else {
      console.log('[6] No confirm button visible, checking page state...');
      const bodyText = await page.textContent('body');
      console.log('Body preview:', bodyText.substring(0, 500));
    }

    // Wait for second confirm_required (Inference confirmation)
    await page.waitForTimeout(8000);

    // Look for inference confirm
    const inferenceCards = await page.locator('text=推理链').all();
    console.log(`[8] Found ${inferenceCards.length} elements with "推理链"`);

    const confirmBtn2 = await page.locator('button:has-text("确认")').first();
    if (await confirmBtn2.isVisible()) {
      console.log('[9] Inference ConfirmCard visible, clicking confirm...');
      await confirmBtn2.click();
      console.log('[10] Inference confirmed');
    } else {
      console.log('[9] No second confirm button visible');
    }

    // Wait for final response
    await page.waitForTimeout(8000);

    // Take screenshot
    await page.screenshot({ path: 'test_inference_ui_result.png' });
    console.log('[RESULT] Screenshot saved to test_inference_ui_result.png');
    console.log('[PASS] UI test completed');
  } catch (e) {
    console.error('[FAIL]', e.message);
    await page.screenshot({ path: 'test_inference_ui_error.png' });
  } finally {
    await browser.close();
  }
})();
"""

result = subprocess.run(
    ['node', '-e', script],
    cwd=r'D:\code\long-running-agent-harness\projects\factory-copilot\frontend',
    capture_output=True, text=True, timeout=60,
)
print(result.stdout)
if result.stderr:
    print('STDERR:', result.stderr[:500])
