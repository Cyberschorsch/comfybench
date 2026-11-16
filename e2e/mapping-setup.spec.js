import {test, expect} from "@playwright/test";

// Setup → Models: the mapping panel of m1 (workflows/example-sdxl.json) against the fixture.

test("mapping fields survive a save", async ({page}) => {
  await page.addInitScript(() => localStorage.setItem("cb2:state", JSON.stringify({view: "setup"})));
  await page.goto("/");
  const fields = page.locator(".nodes .inp select");
  await expect(fields.first()).toBeVisible();

  await fields.first().selectOption("seed");
  await expect(page.locator(".su-bar .dirty")).toContainText("Unsaved changes");
  await expect(page.locator(".nodes .inp.mapped")).toHaveCount(1);

  // Save reloads the setup and drops the cached workflow analysis – the panel must fetch it again
  await page.locator(".su-bar button", {hasText: "Save"}).click();
  await expect(page.locator(".su-bar")).toContainText("All saved");
  await expect(fields.first()).toBeVisible();
  await expect(page.locator(".nodes .inp.mapped")).toHaveCount(1);
  await page.waitForLoadState("networkidle");
});
