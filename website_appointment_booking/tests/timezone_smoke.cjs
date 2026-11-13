// Run against local dev (read-only): NODE_PATH=<existing node_modules> node tests/timezone_smoke.cjs <booking URL>
const assert = require('node:assert/strict');
const {chromium} = require('playwright');

(async () => {
    const url = process.argv[2];
    assert(url, 'Supply a public booking URL');
    const browser = await chromium.launch({headless: true, executablePath: process.env.CHROMIUM_PATH});
    try {
        const context = await browser.newContext({timezoneId: 'America/Los_Angeles', locale: 'en-US'});
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        let navigations = 0;
        page.on('request', request => {
            if (request.isNavigationRequest() && request.frame() === page.mainFrame()) navigations++;
        });
        await page.goto(url);
        const waitZone = zone => page.waitForFunction(
            zone => document.querySelector('#o_wab_slot_data')?.dataset.selectedTz === zone &&
                document.querySelector('.o_wab_calendar')?.getAttribute('aria-busy') !== 'true', zone
        );
        await waitZone('America/Los_Angeles');
        await page.waitForSelector('#o_wab_timezone[data-ready="true"]');
        assert.match(await page.locator('#o_wab_timezone option:checked').innerText(), /Pacific (?:(?:Standard|Daylight) Time \(UTC-0[78]:00\)|Time \(UTC offset varies this month\))/);
        assert(await page.locator('.o_wab_tz_detected').isVisible());
        assert.equal(await page.locator('#o_wab_timezone_search').count(), 0);
        assert(await page.locator('#o_wab_timezone').evaluate(el =>
            el.getBoundingClientRect().width <= 272 && el.getBoundingClientRect().width < el.parentElement.getBoundingClientRect().width
        ));
        const optionCount = await page.locator('#o_wab_timezone option').count();
        const initialSlot = await page.locator('#o_wab_slot_data').evaluate(el => JSON.parse(el.dataset.slots)[0]);
        assert(initialSlot, 'Use a booking page with availability for this smoke check');
        await page.locator('.o_wab_day_available').first().click();
        await page.locator('.o_wab_slot_btn').first().click();
        await page.locator('#o_wab_name').fill('Timezone smoke — not submitted');
        assert(await page.locator('#o_wab_when').inputValue());
        assert(optionCount < 250, `Equivalent cities not removed: ${optionCount}`);
        assert(await page.locator('#o_wab_timezone').evaluate(el =>
            new Set([...el.options].map(option => option.textContent)).size === el.options.length
        ), 'Different DST variants must have distinguishable labels');
        // Exercise actual server grouping: same rules merge, equal winter offsets do not.
        const source = await page.request.get(url);
        const groups = await page.evaluate(html => {
            const doc = new DOMParser().parseFromString(html, 'text/html');
            const group = zone => doc.querySelector(`option[value="${zone}"]`).dataset.tzGroup;
            return [group('America/New_York'), group('America/Detroit'), group('America/Havana'),
                group('America/Denver'), group('America/Phoenix')];
        }, await source.text());
        assert.equal(groups[0], groups[1]);
        assert.notEqual(groups[0], groups[2]);
        assert.notEqual(groups[3], groups[4]);
        await page.locator('#o_wab_timezone').selectOption('America/New_York');
        await waitZone('America/New_York');
        assert.equal(await page.locator('input[name="tz"]').inputValue(), 'America/New_York');
        assert.equal(await page.locator('.o_wab_tz_detected').isVisible(), false);
        assert.equal(await page.locator('#o_wab_name').inputValue(), 'Timezone smoke — not submitted');
        assert.equal(await page.locator('#o_wab_when').inputValue(), '', 'Old slot remained submittable');
        const shiftedSlot = await page.locator('#o_wab_slot_data').evaluate((el, iso) =>
            JSON.parse(el.dataset.slots).find(slot => Date.parse(slot.iso) === Date.parse(iso)), initialSlot.iso
        );
        assert(shiftedSlot, 'Same appointment instant disappeared');
        assert.notEqual(shiftedSlot.time, initialSlot.time, 'Slot time did not change zones');
        assert.equal((await context.cookies()).find(c => c.name === 'tz').value, 'America/New_York');
        assert((await page.locator('.o_wab_month_link').first().getAttribute('href')).includes('tz=America%2FNew_York'));
        assert.equal(navigations, 1, 'Timezone change reloaded the page');

        const saved = await context.newPage();
        await saved.goto(url);
        await saved.waitForSelector('#o_wab_timezone[data-ready="true"]');
        assert.equal(await saved.locator('#o_wab_timezone').inputValue(), 'America/New_York');
        const explicit = new URL(url);
        explicit.searchParams.set('tz', 'US/Pacific');
        await page.goto(explicit.toString());
        await waitZone('US/Pacific');
        assert.equal(await page.locator('#o_wab_timezone').inputValue(), 'US/Pacific', 'Browser/cookie overrode explicit selection');
        await page.route('**/slots?**', route => route.abort());
        await page.locator('#o_wab_timezone').selectOption('Asia/Tokyo');
        await page.waitForFunction(() => document.querySelector('.o_wab_tz_note').textContent.includes('Could not update'));
        assert.equal(await page.locator('#o_wab_timezone').inputValue(), 'US/Pacific');
        assert.equal(navigations, 2, 'Failed refresh navigated away');
        const empty = new URL(url);
        empty.pathname += '/2020/1';
        await saved.goto(empty.toString());
        saved.on('pageerror', error => errors.push(error.message));
        await saved.waitForSelector('#o_wab_timezone[data-ready="true"]');
        await saved.locator('#o_wab_timezone').selectOption('UTC');
        await saved.waitForFunction(() => document.querySelector('#o_wab_slot_data').dataset.selectedTz === 'UTC');
        assert(await saved.locator('.o_wab_empty_month').isVisible());
        assert.equal(await saved.locator('#o_wab_timezone').isDisabled(), false);
        assert.deepEqual(errors, []);
        const summer = new URL(url);
        const futureYear = new Date().getUTCFullYear() + 1;
        summer.pathname += `/${futureYear}/7`;
        summer.searchParams.set('tz', 'America/Los_Angeles');
        await saved.goto(summer.toString());
        await saved.waitForSelector('#o_wab_timezone[data-ready="true"]');
        assert.match(await saved.locator('#o_wab_timezone option:checked').innerText(), /Pacific Daylight Time \(UTC-07:00\)/);
        assert(await saved.locator('.o_wab_tz_detected').isVisible());
        summer.pathname = new URL(url).pathname + `/${futureYear}/3`;
        await saved.goto(summer.toString());
        await saved.waitForSelector('#o_wab_timezone[data-ready="true"]');
        assert.match(await saved.locator('#o_wab_timezone option:checked').innerText(), /Pacific Time \(UTC offset varies this month\)/);
        await saved.setViewportSize({width: 375, height: 800});
        assert(await saved.locator('#o_wab_timezone').evaluate(el =>
            el.getBoundingClientRect().right <= window.innerWidth
        ));
        assert.deepEqual(errors, []);
        console.log(`PASS: ${optionCount} grouped zones; compact width, detected marker, no search, DST-safe grouping, seasonal labels, persistence and failure recovery; no reloads or JS exceptions.`);
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
