import {test, expect} from "@playwright/test";

// The fixture's judge points at http://127.0.0.1:9 – nothing listens there, so a judge job aborts at once.

async function open(page, view){
  await page.addInitScript(v => localStorage.setItem("cb2:state", JSON.stringify({view: v})), view);
  await page.goto("/");
}

test("generate: 'Judge afterwards' goes into the run body", async ({page}) => {
  await open(page, "run");
  const plan = page.waitForRequest(r => r.url().endsWith("/api/run/plan") && r.postDataJSON().judge === true);
  await page.getByText("Judge afterwards").click();
  await plan;
});

test("generate: starting a judge job shows its status and the endpoint error", async ({page}) => {
  await open(page, "run");
  const req = page.waitForRequest(r => r.url().endsWith("/api/judge/start"));
  await page.getByRole("button", {name: "Start judging"}).click();
  const body = (await req).postDataJSON();
  expect(body).toEqual({models: ["m1", "m2", "m3"], prompts: ["apple"], force: false, questions_only: false});
  const status = page.locator("#rn-status");
  await expect(status.locator("h2")).toContainText("Judging");
  await expect(status).toContainText("not reachable");
});

test("setup reloads after a judge job, so new questions are not overwritten", async ({page}) => {
  await open(page, "setup");
  await expect(page.locator(".su-nav button", {hasText: "Prompts"})).toBeVisible();   // setup data loaded once
  let polls = 0;
  await page.route("**/api/judge/start", route => route.fulfill({json: {state: "running", kind: "judge", total: 1, done: 0}}));
  await page.route("**/api/run/status", route => route.fulfill({json: polls++ < 1
    ? {state: "running", kind: "judge", total: 1, done: 0, log: []}
    : {state: "done", kind: "judge", total: 1, done: 1, log: []}}));
  await page.locator("nav button", {hasText: /Generate|Judging/}).click();
  await page.getByText("Questions only").click();
  await page.getByRole("button", {name: "Write questions"}).click();
  await expect(page.locator("#rn-status")).toContainText("judged");
  await expect.poll(() => polls).toBeGreaterThan(1);
  const reload = page.waitForRequest(r => r.url().endsWith("/api/setup") && r.method() === "GET");
  await page.locator("nav button", {hasText: "Setup"}).click();
  await reload;
});

test("setup keeps unsaved config edits after a judge job", async ({page}) => {
  await open(page, "setup");
  await expect(page.locator(".su-nav button", {hasText: "General"})).toBeVisible();   // setup data loaded
  await page.locator(".su-nav button", {hasText: "General"}).click();
  const server = page.locator('input[placeholder="http://127.0.0.1:8188"]');
  await server.fill("http://edited:9999");   // not saved
  let polls = 0;
  await page.route("**/api/judge/start", route => route.fulfill({json: {state: "running", kind: "judge", total: 1, done: 0}}));
  await page.route("**/api/run/status", route => route.fulfill({json: polls++ < 1
    ? {state: "running", kind: "judge", total: 1, done: 0, log: []}
    : {state: "done", kind: "judge", total: 1, done: 1, log: []}}));
  await page.locator("nav button", {hasText: /Generate|Judging/}).click();
  await page.getByRole("button", {name: "Start judging"}).click();
  await expect(page.locator("#rn-status")).toContainText("judged");
  await expect.poll(() => polls).toBeGreaterThan(1);
  await page.locator("nav button", {hasText: "Setup"}).click();
  await page.locator(".su-nav button", {hasText: "General"}).click();
  await expect(page.locator('input[placeholder="http://127.0.0.1:8188"]')).toHaveValue("http://edited:9999");
});

test("setup: judge questions are edited one per line", async ({page}) => {
  await open(page, "setup");
  await page.locator(".su-nav button", {hasText: "Prompts"}).click();
  const ta = page.locator("textarea.su-judge");
  await expect(ta).toHaveValue("Is the apple red?");
  await ta.fill("Is the apple red?\n\n  Is it on a table?  ");
  let saved = null;
  await page.route("**/api/setup/prompts", async route => {
    saved = route.request().postDataJSON();
    await route.fulfill({json: {ok: true}});
  });
  await page.locator(".su-bar button", {hasText: "Save"}).click();
  await expect.poll(() => saved && saved.prompts[0].judge).toEqual(["Is the apple red?", "Is it on a table?"]);
});
