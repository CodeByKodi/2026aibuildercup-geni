import { expect, type Page } from "@playwright/test";

const IGNORE = /favicon|fonts\.g|net::err|failed to load resource/i;

/** Opens the app and records console errors for assertNoErrors. */
export async function open(page: Page, path = "/") {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => { if (m.type() === "error" && !IGNORE.test(m.text())) errors.push(m.text()); });
  const response = await page.goto(path, { waitUntil: "domcontentloaded" });
  await expect(page.getByText("WorkPilot AI").first()).toBeVisible({ timeout: 60_000 });
  return { response, errors };
}

export function assertNoErrors(errors: string[]) {
  expect(errors.filter((e) => !IGNORE.test(e)), errors.join("\n")).toEqual([]);
}

export const tid = (page: Page, id: string) => page.locator(`[data-testid="${id}"]`);

export function sessionId(page: Page): string {
  return new URL(page.url()).searchParams.get("s") || "";
}

/** Start → Knowledge: ask the question (a sample scenario or a typed one). */
export async function ask(page: Page, opts: { scenario?: string; question?: string } = {}) {
  await open(page, "/");
  if (opts.scenario) await tid(page, `scenario-${opts.scenario}`).click();
  if (opts.question) await tid(page, "question").fill(opts.question);
  await tid(page, "analyze-with-workpilot").click();
  await expect(tid(page, "step-knowledge")).toBeVisible({ timeout: 90_000 });
  await expect(tid(page, "source-card").first()).toBeVisible();
  expect(sessionId(page)).toMatch(/^SES-/);
}

/** Knowledge → Analyze: run the agent's Decide turn and wait for the findings. */
export async function analyze(page: Page) {
  await tid(page, "next-analyze").click();
  await expect(tid(page, "step-analyze")).toBeVisible();
  await expect(tid(page, "analysis-progress")).toBeVisible();
  await expect(tid(page, "next-decision").or(tid(page, "no-answer"))).toBeVisible({ timeout: 200_000 });
}

/** Analyze → Decision → Actions: the agent proposes Action Contracts. */
export async function prepareContracts(page: Page) {
  await tid(page, "next-decision").click();
  await expect(tid(page, "decision-record")).toBeVisible();
  await tid(page, "prepare-contracts").click();
  await expect(tid(page, "step-actions")).toBeVisible({ timeout: 200_000 });
  await expect(tid(page, "contract").first()).toBeVisible();
}

export async function toContracts(page: Page) {
  await ask(page, { scenario: "go-live" });
  await analyze(page);
  await prepareContracts(page);
}

export async function step(page: Page, id: string) {
  await tid(page, `stepper-${id}`).click();
  await expect(tid(page, `step-${id}`)).toBeVisible();
}

/** Never claim execution without a receipt from Gmail or Calendar. */
export async function assertExecutionBacked(page: Page) {
  const rows = tid(page, "approval-row");
  for (let i = 0; i < (await rows.count()); i++) {
    const row = rows.nth(i);
    if ((await row.getAttribute("data-execution")) === "Executed") {
      await expect(row.locator('[data-testid="receipt"]')).toBeVisible();
    }
  }
}
