import {test, expect} from "@playwright/test";

// Setup → Judge against the fixture (judge: url http://127.0.0.1:9/v1, model fake-vl, one quality question).

async function openJudge(page){
  await page.addInitScript(() => localStorage.setItem("cb2:state", JSON.stringify({view: "setup"})));
  await page.goto("/");
  await page.locator(".su-nav button", {hasText: "Judge"}).click();
}

test("judge settings are editable and saved to config", async ({page}) => {
  await openJudge(page);
  await expect(page.locator("#jOn")).toBeChecked();
  await expect(page.locator("#jUrl")).toHaveValue("http://127.0.0.1:9/v1");
  await expect(page.locator("#jModel")).toHaveValue("fake-vl");
  await expect(page.locator("#jQuality")).toHaveValue("Is it clean?");

  await page.locator("#jQuality").fill("Is it clean?\nIs it sharp?");
  await expect(page.locator(".su-bar .dirty")).toContainText("Unsaved changes");
  await page.locator(".su-bar button", {hasText: "Save"}).click();
  await expect(page.locator(".su-bar")).toContainText("All saved");
  const cfg = (await (await page.request.get("/api/setup")).json()).config;
  expect(cfg.judge.quality).toEqual(["Is it clean?", "Is it sharp?"]);

  // restore for the other specs (same fixture server)
  await page.locator("#jQuality").fill("Is it clean?");
  await page.locator(".su-bar button", {hasText: "Save"}).click();
  await expect(page.locator(".su-bar")).toContainText("All saved");
  await page.waitForLoadState("networkidle");   // Save reloads setup and data; don't close the page mid-request
});

test("switching the judge off and on keeps the values until saved", async ({page}) => {
  await openJudge(page);
  await page.locator("#jOn").uncheck();
  await expect(page.locator("#jUrl")).toHaveCount(0);
  await page.locator("#jOn").check();
  await expect(page.locator("#jModel")).toHaveValue("fake-vl");
});

test("api key is saved, never shown, and removable", async ({page}) => {
  await openJudge(page);
  await expect(page.locator("#jKeyState")).toContainText("No key set");
  await page.locator("#jKey").fill("sk-e2e-secret");
  await page.getByRole("button", {name: "Save key"}).click();
  await expect(page.locator("#jKeyState")).toContainText("Key saved");
  await expect(page.locator("#jKey")).toHaveValue("");
  const raw = await (await page.request.get("/api/setup")).text();
  expect(raw).not.toContain("sk-e2e-secret");
  await page.getByRole("button", {name: "Remove"}).click();
  await expect(page.locator("#jKeyState")).toContainText("No key set");
});

test("test connection reports an unreachable judge", async ({page}) => {
  await openJudge(page);
  await page.locator("#jTest").click();
  await expect(page.locator("#jTestRes")).toContainText("not reachable");
  await expect(page.locator("#jTestRes")).toHaveClass(/err/);
});
