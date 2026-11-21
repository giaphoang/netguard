const assert = require("node:assert/strict");
const { chromium } = require("playwright-core");

async function verify(browser, url, viewport) {
  const page = await browser.newPage({ viewport });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  try {
    await page.route("**/*", route => {
      const request = new URL(route.request().url());
      if (request.origin !== url.origin || request.pathname.startsWith("/api/")) {
        errors.push(`Unexpected runtime request to ${request.href}`);
        return route.abort();
      }
      return route.continue();
    });
    assert.equal((await page.goto(url.href)).status(), 200);
    await page.getByRole("heading", { name: "Ubuntu network failover" }).waitFor();
    await page.getByRole("heading", { name: "Healthy baseline" }).waitFor();
    for (const seconds of ["12.1 s", "16.4 s"]) {
      await page.getByText(seconds, { exact: true }).waitFor();
    }
    assert.equal(await page.locator('td[data-result="success"]').count(), 4);

    const stage = page.getByRole("combobox", { name: "Recording stage" });
    if (viewport.width > 1000) {
      await page.getByRole("button", { name: "Play incident" }).click();
      await page.getByRole("heading", { name: "Link failure" }).waitFor();
      await page.getByRole("button", { name: "Pause playback" }).click();
    } else {
      await stage.selectOption({ label: "Link failure" });
    }
    assert.equal(await page.locator('td[data-result="failed"]').count(), 4);

    await stage.selectOption({ label: "Failover verified" });
    await page.getByText("qwen2.5:1.5b", { exact: true }).waitFor();
    await page.getByText("Observation for leaf-1/spine-1", { exact: true }).waitFor();
    assert.equal(await page.getByRole("cell", { name: "Spine 2", exact: true }).count(), 2);
    assert.equal(await page.locator('td[data-result="success"]').count(), 4);
    assert.equal(await page.getByRole("link", { name: "View the Ubuntu run on GitHub" }).count(), 1);

    await stage.selectOption({ label: "Redundancy restored" });
    assert.equal(await page.getByRole("cell", { name: "Spine 1, Spine 2", exact: true }).count(), 2);
    await page.locator('.react-flow__node[data-id="leaf-1"]').click();
    await page.getByRole("heading", { name: "Recorded BGP peers" }).waitFor();
    assert.match(await page.locator(".inspection pre").first().textContent(), /"Established"/);
    assert.equal(await page.getByRole("button", { name: "Simulate selected link" }).count(), 0);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    assert.deepEqual(errors, []);
  } finally {
    await page.close();
  }
}

async function main() {
  assert.ok(process.argv[2], "Supply the demo URL as the first argument.");
  const url = new URL(process.argv[2]);
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  try {
    await verify(browser, url, { width: 1440, height: 1000 });
    await verify(browser, url, { width: 390, height: 844 });
    console.log("Desktop and mobile passed playback, recorded probes, Qwen evidence, and router inspection with API requests blocked.");
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
