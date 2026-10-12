import { expect, test } from "@playwright/test";
import { analyze, ask, assertExecutionBacked, assertNoErrors, open, prepareContracts, sessionId, step, tid } from "./helpers";

test.describe("React · question to human gate", () => {
  test("analysis, decision record and contracts agree, and the agent pauses for a person", async ({ page }) => {
    await ask(page, { scenario: "go-live" });
    await analyze(page);

    // Live checklist: every reported stage carries a server-measured time.
    const progress = tid(page, "analysis-progress");
    await expect(progress).toContainText("Searching enterprise sources");
    await expect(progress).toContainText(/Total, measured on the server\s*\d+(\.\d)? s/);

    // Key findings: NOT READY with counts that match what is on screen.
    await expect(tid(page, "verdict-status")).toHaveText(/NOT READY/);
    const blockers = await tid(page, "blocker-card").count();
    expect(blockers).toBeGreaterThan(0);
    await expect(tid(page, "verdict").locator(".stats div").first()).toContainText(String(blockers));
    await expect(tid(page, "produced-by")).toBeVisible();

    await prepareContracts(page);
    const contracts = await tid(page, "contract").count();
    expect(contracts).toBeGreaterThan(0);

    await step(page, "decision");
    await expect(tid(page, "decision-id")).toHaveText(/^DEC-/);
    const decisionId = await tid(page, "decision-id").innerText();
    expect(await tid(page, "approval-conditions").locator("li").count()).toBeGreaterThan(0);
    await expect(tid(page, "evidence-map")).toBeVisible();

    await step(page, "approval");
    const rows = tid(page, "approval-row");
    await expect(rows).toHaveCount(contracts);
    for (let i = 0; i < contracts; i++) await expect(rows.nth(i)).toHaveAttribute("data-approval", "Pending");
    await expect(tid(page, "agent-status")).toHaveAttribute("data-mode", "paused");
    await expect(tid(page, "agent-status")).toContainText(`waiting for a person to approve ${contracts} of ${contracts}`);
    await expect(tid(page, "next-outcome")).toBeDisabled();
    await assertExecutionBacked(page);

    // The same link restores the same workflow from Firestore.
    const sid = sessionId(page);
    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(tid(page, "step-approval")).toBeVisible({ timeout: 60_000 });
    await expect(tid(page, "approval-row")).toHaveCount(contracts);
    expect(sessionId(page)).toBe(sid);

    const second = await page.context().newPage();
    const { errors } = await open(second, `/?s=${sid}&step=decision`);
    await expect(tid(second, "decision-id")).toHaveText(decisionId);
    assertNoErrors(errors);
    await second.close();
  });

  test("approval and rejection persist, and execution is never claimed without a receipt", async ({ page }) => {
    await ask(page, { scenario: "go-live" });
    await analyze(page);
    await prepareContracts(page);
    await step(page, "approval");
    const rows = tid(page, "approval-row");
    const n = await rows.count();
    test.skip(n < 2, "needs two contracts to approve one and reject one");

    await rows.nth(0).locator('[data-testid="approve"]').click();
    await expect(rows.nth(0)).toHaveAttribute("data-approval", "Approved");
    await rows.nth(1).locator('[data-testid="reject"]').click();
    await expect(rows.nth(1)).toHaveAttribute("data-approval", "Rejected");
    await expect(tid(page, "tab-approved")).toContainText("(1)");
    await expect(tid(page, "tab-rejected")).toContainText("(1)");
    await expect(rows.nth(0)).toHaveAttribute("data-execution", "Not started");
    await assertExecutionBacked(page);

    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(tid(page, "step-approval")).toBeVisible({ timeout: 60_000 });
    await expect(tid(page, "approval-row").nth(0)).toHaveAttribute("data-approval", "Approved");
    await expect(tid(page, "approval-row").nth(1)).toHaveAttribute("data-approval", "Rejected");

    // Outcomes can only be recorded for executed work.
    await step(page, "outcome");
    await expect(tid(page, "outcome-row").first()).toBeVisible();
    await expect(tid(page, "outcome-form")).toHaveCount(0);
    await expect(page.getByText(/Run this approved action on Approval before recording its outcome/).first()).toBeVisible();
    // Outcome says what is next and offers the run itself, instead of a resume button with nothing to resume for.
    await expect(tid(page, "next-step")).toHaveAttribute("data-next", "run");
    await expect(tid(page, "run-all-outcome")).toBeVisible();
    await expect(tid(page, "resume-agent")).toHaveCount(0);
    await expect(page.getByRole("button", { name: /Demo Outcomes/ })).toHaveCount(0);
    await expect(tid(page, "impact-hours")).toHaveText(/\d+(\.\d+)?\s*(hrs?|min)/);
  });

  test("backward navigation keeps generated state", async ({ page }) => {
    await ask(page, { scenario: "go-live" });
    await analyze(page);
    await prepareContracts(page);
    for (const id of ["decision", "analyze", "knowledge", "start", "actions"]) await step(page, id);
    await expect(tid(page, "contract").first()).toBeVisible();
    await expect(tid(page, "stepper-analyze").locator(".dot svg")).toBeVisible(); // done tick
  });
});
