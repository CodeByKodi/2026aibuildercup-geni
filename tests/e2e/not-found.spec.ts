import { expect, test } from "@playwright/test";
import { analyze, ask, tid } from "./helpers";

test.describe("React · questions the workspace cannot answer", () => {
  test("an out-of-scope question records no decision and no blockers", async ({ page }) => {
    await ask(page, { question: "What is the disaster-recovery budget approved for the v13 data centre migration?" });
    // Either the search finds nothing (Knowledge stops here) or the agent finds no answer in what it found.
    if (await tid(page, "next-analyze").isEnabled()) await analyze(page);
    await expect(tid(page, "no-answer")).toBeVisible();
    await expect(tid(page, "blocker-card")).toHaveCount(0);
    await expect(tid(page, "next-decision")).toHaveCount(0);
    await expect(tid(page, "stepper-decision")).toBeDisabled();
  });
});
