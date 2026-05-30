const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });

  await page.goto('http://localhost:5001', { waitUntil: 'networkidle' });
  console.log('[1] Page loaded');

  await page.waitForSelector('textarea, input[type="text"], [contenteditable="true"]', { timeout: 10000 });
  console.log('[2] Chat input found');

  const input = page.locator('textarea, input[type="text"], [contenteditable="true"]').first();
  await input.fill('查询所有工单');
  console.log('[3] Message typed');

  const sendBtn = page.locator('button[type="submit"], button:has-text("发送")').first();
  if (await sendBtn.isVisible()) {
    await sendBtn.click();
  } else {
    await input.press('Enter');
  }
  console.log('[4] Message sent');

  await page.waitForTimeout(15000);

  // Expand collapsed execution chain to read all steps
  const chainToggle = page.locator('text=/\\d+\\/\\d+/').first();
  if (await chainToggle.isVisible().catch(() => false)) {
    await chainToggle.click();
    await page.waitForTimeout(500);
  }

  const fullText = await page.locator('body').innerText();

  // Count how many work order IDs appear in the response
  const woMatches = fullText.match(/WO-\d+/g) || [];
  const uniqueWOs = [...new Set(woMatches)];
  console.log(`[5] Unique WO IDs found: ${JSON.stringify(uniqueWOs)}`);
  console.log(`[5] Total unique WOs: ${uniqueWOs.length}`);

  // Extract execution chain from HTML
  const html = await page.content();
  // Find the execution chain steps section in HTML
  const chainIdx = html.indexOf('执行链路');
  if (chainIdx >= 0) {
    // Extract step labels and their following span (detail)
    const stepRegex = /(路由分析|意图识别[^<]*|匹配工具[^<]*|参数提取|数据过滤[^<]*|执行[^<]*|查询结果[^<]*|LLM 格式化[^<]*|执行完成)/g;
    let m;
    const steps = [];
    while ((m = stepRegex.exec(html.substring(chainIdx))) !== null) {
      // Find the detail span after this step
      const afterStep = html.substring(chainIdx + m.index + m[0].length, chainIdx + m.index + m[0].length + 300);
      const detailMatch = afterStep.match(/(\{[^}]*\}|来源[^<]*|Agent[^<]*|基于[^<]*|将[^<]*|共[^<]*|无[^<]*|候选[^<]*)/);
      const detail = detailMatch ? detailMatch[0].replace(/<[^>]+>/g, '').trim() : '(no detail)';
      steps.push({ label: m[0].replace(/<[^>]+>/g, '').trim(), detail });
    }
    console.log(`[5] Execution chain (${steps.length} steps):`);
    steps.forEach((s, i) => {
      console.log(`   ${i+1}. ${s.label} ${s.detail !== '(no detail)' ? '→ ' + s.detail : ''}`);
    });
  }
  const hasMachineShop = fullText.includes('机加车间');
  const hasAssembly = fullText.includes('装配车间');
  const isFiltered = !hasAssembly || (hasMachineShop && uniqueWOs.length < 5);
  console.log(`[6] Has 机加车间: ${hasMachineShop}`);
  console.log(`[6] Has 装配车间: ${hasAssembly}`);
  console.log(`[6] FILTER WORKING: ${isFiltered ? 'YES (data filtered by workshop)' : 'NO (unfiltered)'}`);

  if (isFiltered) {
    console.log('[PASS] Data authorization filter is working correctly.');
  } else {
    console.log('[FAIL] Data authorization filter is NOT working.');
  }

  await page.screenshot({ path: '/tmp/chat-response.png', fullPage: true });
  await browser.close();
  console.log('[7] Done');
})();
