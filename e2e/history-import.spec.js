import {test, expect} from "@playwright/test";

// Setup → Models: "From ComfyUI history …" picks a workflow ComfyUI ran and stores it in workflows/.
// The ComfyUI side (api/comfy/history) is stubbed in the browser; the save goes to the real fixture server.

const wf = {
  "4": {class_type: "CheckpointLoaderSimple", inputs: {ckpt_name: "flux1-dev.safetensors"}},
  "6": {class_type: "CLIPTextEncode", inputs: {text: "portrait of an old fisherman", clip: ["4", 1]}},
  "5": {class_type: "EmptyLatentImage", inputs: {width: 1024, height: 1024, batch_size: 1}},
  "3": {class_type: "KSampler", inputs: {seed: 5, steps: 20, cfg: 1, sampler_name: "euler", scheduler: "simple",
        denoise: 1, model: ["4", 0], positive: ["6", 0], negative: ["6", 0], latent_image: ["5", 0]}},
  "9": {class_type: "SaveImage", inputs: {filename_prefix: "ComfyUI", images: ["3", 0]}},
};

test("import a workflow from ComfyUI history", async ({page}) => {
  await page.route("**/api/comfy/history", r => r.fulfill({json: {ok: true, items: [
    {prompt_id: "b", time: 1763291000000, status: "success", prompt: "portrait of an old fisherman",
     model: "flux1-dev.safetensors", nodes: 5, has_output: true, runs: 2, workflow: wf}]}}));
  await page.addInitScript(() => localStorage.setItem("cb2:state", JSON.stringify({view: "setup"})));
  await page.goto("/");
  const sel = page.locator("select:has(option[value=__history])").first();
  await expect(sel).toBeVisible();

  await sel.selectOption("__history");
  const item = page.locator("dialog .cb-hist-item");
  await expect(item).toHaveCount(1);
  await expect(item).toContainText("portrait of an old fisherman");
  await expect(item).toContainText("flux1-dev.safetensors · 5 nodes · 2 runs");
  await item.click();

  const name = page.locator("dialog input[type=text]");
  await expect(name).toHaveValue("flux1-dev.json");
  await page.locator("dialog button.btn-p").click();

  await expect(sel).toHaveValue("workflows/flux1-dev.json");
  await expect(page.locator(".su-bar .dirty")).toContainText("Unsaved changes");
  await expect(page.locator(".nodes .inp").first()).toBeVisible();   // mapping panel analyzed the new file
  await page.waitForLoadState("networkidle");
});
