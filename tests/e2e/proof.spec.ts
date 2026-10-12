import { expect, test } from "@playwright/test";
import { analyze, ask, prepareContracts, step, tid } from "./helpers";

test.describe("React · impact, evidence and Google Cloud proof", () => {
  test("strip, citations, follow-up previews and cloud proof all agree with the recorded workflow", async ({ page }) => {
    await ask(page, { scenario: "go-live" });
    await analyze(page);
    await expect(tid(page, "ps-docs")).toContainText("6");
    const blockers = await tid(page, "blocker-card").count();
    await expect(tid(page, "ps-blockers")).toContainText(String(blockers));
    await expect(tid(page, "ps-passages")).not.toContainText("—");
    await expect(tid(page, "ps-analysis")).toContainText(/\d+(\.\d)? s/);
    await expect(tid(page, "ps-gate")).toContainText("0");
    await expect(tid(page, "ps-manual")).toContainText(/\d+ min/);
    const baseline = (await tid(page, "ps-manual").locator("b").innerText()).trim();

    // A citation opens the exact passages behind it and the claims it supports.
    await tid(page, "blocker-card").first().locator('[data-testid="cite"]').first().click();
    const drawer = tid(page, "evidence-drawer");
    await expect(drawer).toBeVisible();
    await expect(drawer.locator('[data-testid="drawer-claims"] li').first()).toBeVisible();
    await expect(drawer.locator('[data-testid="drawer-passage"]').or(drawer.locator('[data-testid="drawer-no-passage"]')).first()).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(drawer).toHaveCount(0);

    await prepareContracts(page);
    const title = await tid(page, "contract").first().locator("b").first().innerText();
    await expect(tid(page, "precondition").first()).toContainText("only after a person approves");
    await tid(page, "contract").first().locator('[data-testid="preview-gmail"]').click();
    const gmail = tid(page, "gmail-preview");
    await expect(gmail).toContainText(title);
    await expect(gmail).toContainText("nothing sent to Google");
    await expect(tid(page, "contract").first()).toContainText("Not started"); // a preview runs nothing
    await tid(page, "contract").first().locator('[data-testid="preview-audit"]').click();
    await expect(tid(page, "audit-preview")).toBeVisible();
    await expect(tid(page, "ps-manual")).toContainText(/\d+ min/);
    await expect(tid(page, "ps-workpilot")).toContainText(/\d+(\.\d)? s/);
    // The baseline is fixed when the decision is recorded: preparing contracts does not move it.
    await expect(tid(page, "ps-manual")).toContainText(baseline);

    await step(page, "decision");
    const decisionId = await tid(page, "decision-id").innerText();
    const proof = tid(page, "gcp-proof");
    await proof.locator("summary").click();
    await expect(proof).toContainText(decisionId);
    await expect(proof.locator('[data-testid="gcp-row"]')).toHaveCount(7);
    await expect(proof.locator('[data-testid="gcp-row"]', { hasText: "Vertex AI Search" })).toContainText(/\d{2}:\d{2}/);
  });
});
