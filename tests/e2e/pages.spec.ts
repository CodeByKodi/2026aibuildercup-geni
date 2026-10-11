import { expect, test } from "@playwright/test";
import { analyze, ask, open, sessionId, tid } from "./helpers";

test.describe("React · sidebar pages", () => {
  test("System Status shows each service honestly; Agent Engine is not used", async ({ page }) => {
    await open(page, "/");
    await tid(page, "nav-status").click();
    const table = tid(page, "status-table");
    await expect(table).toBeVisible();
    const row = (name: string) => table.locator("tbody tr").filter({ has: page.getByRole("cell", { name, exact: true }) });
    await expect(row("Vertex AI Agent Engine")).toContainText("NOT USED");
    await expect(row("Cloud Run")).toBeVisible();
    // Gmail can only be ACTIVE when Approval's run buttons are enabled, and vice versa.
    const gmail = await row("Gmail (drafts only)").innerText();
    const cfg = await (await page.request.get("/api/config")).json();
    expect(/ACTIVE/.test(gmail)).toBe(cfg.followups_connected);
  });

  test("My Sessions and Decision Memory list this browser's analysis", async ({ page }) => {
    await ask(page, { scenario: "uat-perf" });
    await analyze(page);
    const sid = sessionId(page);
    await tid(page, "nav-sessions").click();
    await expect(tid(page, "sessions-table")).toContainText(/p95/);
    await expect(tid(page, "sessions-table").locator(`a[href="/?s=${sid}"]`)).toBeVisible();
    await tid(page, "nav-memory").click();
    await expect(tid(page, "memory-entry").first()).toContainText(/DEC-/);
    await tid(page, "nav-new").click();
    await expect(tid(page, "step-analyze").or(tid(page, "step-start"))).toBeVisible();
  });
});
