import {test, expect} from "@playwright/test";

// Setup → Models: "＋ Workflow for mode …" offers txt2img / img2img / custom –
// only "custom" reveals the free-text field for the mode name.

async function openDialog(page){
  await page.addInitScript(() => localStorage.setItem("cb2:state", JSON.stringify({view: "setup"})));
  await page.goto("/");
  await page.locator(".su-list button", {hasText: "Model One"}).click();
  await page.locator("button", {hasText: "Workflow for mode"}).click();
  const dlg = page.locator("dialog.dlg");
  await expect(dlg).toBeVisible();
  return dlg;
}

test("the mode dialog lists txt2img, img2img and custom", async ({page}) => {
  const dlg = await openDialog(page);
  const picks = dlg.locator(".dlg-pick label");
  await expect(picks).toHaveCount(3);
  await expect(picks.nth(0)).toContainText("txt2img");
  await expect(picks.nth(1)).toContainText("img2img");
  await expect(picks.nth(2)).toContainText("custom");
  await expect(dlg.locator('input[value="img2img"]')).toBeChecked();
  // no free-text field until "custom" is chosen
  await expect(dlg.locator("input[type=text]")).toHaveCount(0);
  await expect(dlg.locator(".dlg-err")).toBeEmpty();
});

test("custom asks for the name and adds the mode", async ({page}) => {
  const dlg = await openDialog(page);
  await dlg.locator(".dlg-pick label", {hasText: "custom"}).click();
  const name = dlg.locator("input[type=text]");
  await expect(name).toBeVisible();
  await name.fill("inpaint");
  await dlg.locator("button.btn-p").click();
  await expect(dlg).toHaveCount(0);

  const row = page.locator(".su-edit table.kv tr", {hasText: "inpaint"});
  await expect(row).toBeVisible();
  await expect(page.locator(".su-bar .dirty")).toContainText("Unsaved changes");
});

test("custom without a name stays in the dialog", async ({page}) => {
  const dlg = await openDialog(page);
  await dlg.locator(".dlg-pick label", {hasText: "custom"}).click();
  await dlg.locator("button.btn-p").click();
  await expect(dlg.locator(".dlg-err")).toHaveText("Please enter a name for the custom mode");
  await expect(dlg).toBeVisible();

  await dlg.locator("input[type=text]").fill("inpaint");
  await expect(dlg.locator(".dlg-err")).toBeEmpty();
  await dlg.locator("button.btn-p").click();
  await expect(dlg).toHaveCount(0);
});
