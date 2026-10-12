import { expect, test, type Page } from "@playwright/test";
import { analyze, ask, assertExecutionBacked, open, prepareContracts, sessionId, step, tid } from "./helpers";

/**
 * The three-minute judge path, from a fresh session to READY FOR APPROVAL, with a reload
 * after every stage. Real follow-ups: each approved contract becomes a Gmail draft (never
 * sent) in the connected mailbox, so it is opt-in: WORKPILOT_E2E_EXECUTE=1.
 */
test.describe("React · three-minute demo path", () => {
  test.skip(!process.env.WORKPILOT_E2E_EXECUTE, "set WORKPILOT_E2E_EXECUTE=1 to create real Gmail drafts");
  test.setTimeout(600_000);

  const reload = async (page: Page, stepId: string) => {
    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(tid(page, `step-${stepId}`)).toBeVisible({ timeout: 60_000 });
  };

  test("question to READY FOR APPROVAL, surviving a reload at every stage", async ({ page, browser }) => {
    await ask(page, { scenario: "go-live" });
    await analyze(page);
    await expect(tid(page, "verdict-status")).toHaveText(/NOT READY/);
    const blockers = await tid(page, "blocker-card").count();
    await reload(page, "analyze");
    await expect(tid(page, "blocker-card")).toHaveCount(blockers);

    await prepareContracts(page);
    const sid = sessionId(page);
    console.log(`session ${sid}`);
    await step(page, "approval");
    const rows = tid(page, "approval-row");
    const n = await rows.count();

    // Approval: a person approves, the agent waits.
    await tid(page, "approve-all").click();
    for (let i = 0; i < n; i++) await expect(rows.nth(i)).toHaveAttribute("data-approval", "Approved");
    await expect(tid(page, "agent-status")).toHaveAttribute("data-mode", "paused");
    await reload(page, "approval");
    for (let i = 0; i < n; i++) await expect(tid(page, "approval-row").nth(i)).toHaveAttribute("data-approval", "Approved");

    // Execution: one click, a real receipt per action.
    await tid(page, "run-all").click();
    for (let i = 0; i < n; i++) await expect(tid(page, "approval-row").nth(i)).toHaveAttribute("data-execution", "Executed", { timeout: 90_000 });
    await assertExecutionBacked(page);
    await reload(page, "approval");
    await assertExecutionBacked(page);

    // Outcomes: a person writes what happened, the agent proposes, the person confirms.
    await tid(page, "next-outcome").click();
    await expect(tid(page, "readiness-status")).toHaveText(/NOT READY/);
    await expect(tid(page, "next-step")).toHaveAttribute("data-next", "confirm");
    await expect(tid(page, "receipt-line")).toHaveCount(n);
    const forms = tid(page, "outcome-form");
    await expect(forms).toHaveCount(n);
    for (let i = 0; i < n; i++) {
      await forms.nth(i).locator('[data-testid="actual-outcome"]').fill(
        "E2E validation run: the owner verified the fix in staging and attached the evidence to the ticket.");
    }
    await tid(page, "ask-agent-all").click();
    for (let i = 0; i < n; i++) await expect(forms.nth(i).locator('[data-testid="suggestion"]')).toBeVisible({ timeout: 180_000 });
    for (let i = 0; i < n; i++) await forms.nth(i).locator('[data-testid="outcome-status"]').selectOption("ACHIEVED"); // the person decides
    await tid(page, "confirm-all").click();
    await expect(tid(page, "outcome-form")).toHaveCount(0, { timeout: 90_000 });
    await expect(tid(page, "outcome-evidence")).toHaveCount(n);

    const conditions = await tid(page, "conditions").locator("li").count();
    const expected = n >= conditions ? "READY FOR APPROVAL" : "AT RISK";
    await expect(tid(page, "readiness-status")).toHaveText(expected);
    await expect(tid(page, "before-after")).toContainText("NOT READY");

    // Confirming the last outcome resumes the agent from Firestore by itself; no button to press.
    await expect(tid(page, "agent-message")).toHaveAttribute("data-ok", "true", { timeout: 240_000 });
    await expect(tid(page, "resume-agent")).toHaveCount(0);
    await expect(tid(page, "next-step")).toHaveCount(0);
    await expect(tid(page, "agent-message")).toContainText(expected === "READY FOR APPROVAL" ? /ready for approval/i : /AT RISK/);

    // Impact with its formula, and the audit trail behind it.
    await expect(tid(page, "impact-hours")).toHaveText(/\d+(\.\d+)?\s*(hrs?|min)/);
    await tid(page, "impact-method").locator("summary").click();
    await expect(tid(page, "impact-method")).toContainText(/words per minute|wpm|minutes/i);
    await tid(page, "audit").locator("summary").click();
    await expect(tid(page, "audit")).toContainText("Outcome confirmed");
    await expect(tid(page, "audit")).toContainText("Gmail draft created");

    // Everything survives a reload.
    await reload(page, "outcome");
    await expect(tid(page, "readiness-status")).toHaveText(expected);
    await expect(tid(page, "outcome-evidence")).toHaveCount(n);
    await expect(tid(page, "agent-message")).toHaveAttribute("data-ok", "true");
    await page.screenshot({ path: "test-results/demo-outcome.png", fullPage: true });

    // Backward through every step, then forward again: the recorded data stays the same.
    for (const id of ["approval", "actions", "decision", "analyze", "knowledge", "start"]) await step(page, id);
    await step(page, "analyze");
    await expect(tid(page, "blocker-card")).toHaveCount(blockers);
    await step(page, "actions");
    await expect(tid(page, "contract")).toHaveCount(n);
    await step(page, "approval");
    for (let i = 0; i < n; i++) await expect(tid(page, "approval-row").nth(i)).toHaveAttribute("data-execution", "Executed");
    await step(page, "outcome");
    await expect(tid(page, "readiness-status")).toHaveText(expected);
    await expect(tid(page, "outcome-evidence")).toHaveCount(n);

    // Decision Memory: expected and actual. My Sessions: the stage and readiness.
    await tid(page, "nav-memory").click();
    const memory = tid(page, "memory-entry").first();
    await expect(memory).toContainText(/Expected: /);
    await expect(memory).toContainText(/Actual: .*ACHIEVED/);
    await tid(page, "nav-sessions").click();
    const row = tid(page, "sessions-table").locator("tr", { has: page.locator(`a[href="/?s=${sid}"]`) });
    await expect(row).toContainText("Outcomes recorded");
    await expect(row).toContainText(expected);

    // A phone opening the finished session loads past the spinner straight to the result.
    const phone = await browser.newPage({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await open(phone, `/?s=${sid}`);
    await expect(tid(phone, "step-outcome")).toBeVisible({ timeout: 60_000 });
    await expect(tid(phone, "readiness-status")).toHaveText(expected);
    await phone.close();
  });
});
