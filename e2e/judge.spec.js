import {test, expect} from "@playwright/test";
import path from "node:path";

// Fixture (e2e/fixture.py): m1 judged with one "no", m2 judged but stale, m3 not judged.

async function open(page, view){
  await page.addInitScript(v => localStorage.setItem("cb2:state", JSON.stringify({view: v})), view);
  await page.goto("/");
}

test("compare shows badges and judge averages", async ({page}) => {
  await open(page, "grid");
  const badges = page.locator("#grid .jb");
  await expect(badges).toHaveCount(2);
  await expect(page.locator("#grid .jb:not(.stale)")).toHaveText("0/1 · 1/1");
  await expect(page.locator("#grid .jb.stale")).toHaveText("1/1 · 1/1");
  await expect(page.locator("#grid .jb:not(.stale)")).toHaveAttribute("title", /✗ Is the apple red\?/);
  const foot = page.locator("#grid tfoot");
  await expect(foot).toContainText("Ø Fidelity (judge)");
  await expect(foot).toContainText("Ø Quality (judge)");
  await expect(foot.locator("tr", {hasText: "Ø Fidelity"})).toContainText("0%");
});

test("gallery sorts by worst fidelity and shows agreement", async ({page}) => {
  await open(page, "gallery");
  await expect(page.locator("#gal figure").first()).toContainText("Model Three");   // newest first
  await page.locator("#gSort").selectOption("judge");
  await expect(page.locator("#gal figure").first()).toContainText("Model One");
  await expect(page.locator("#gal figure").nth(1)).toContainText("Model Two");
  await expect(page.locator("#view-gallery")).toContainText("Agreement with your stars");
});

test("lightbox lists the judge answers", async ({page}) => {
  await open(page, "gallery");
  await page.locator("#gal figure", {hasText: "Model One"}).locator("img").click();
  const lb = page.locator("#lb.open aside");
  await expect(lb).toContainText("Judge");
  await expect(lb).toContainText("✗ Is the apple red?");
  await expect(lb).toContainText("✓ Is it clean?");
  await expect(lb).toContainText("fake-vl");
});

test("static report shows the badge", async ({page}) => {
  await page.goto("file://" + path.resolve("e2e/.home/results/index.html"));
  await expect(page.locator("#grid .jb").first()).toBeVisible();
});

test("judge UI stays hidden without a judge section", async ({page}) => {
  // Fetch once and reuse: the status poll may reload /api/data while the test is already finishing.
  const d = await (await page.request.get("/api/data")).json();
  d.judge_enabled = false;
  d.judge = {};
  await page.route("**/api/data", route => route.fulfill({json: d}));
  await open(page, "grid");
  await expect(page.locator("#grid img").first()).toBeVisible();
  await expect(page.locator("#grid .jb")).toHaveCount(0);
  await expect(page.locator("#grid tfoot")).not.toContainText("judge");
  await page.unrouteAll({behavior: "ignoreErrors"});
});
