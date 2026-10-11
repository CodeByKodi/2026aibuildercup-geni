import { expect, test } from "@playwright/test";
import { assertNoErrors, open, tid } from "./helpers";

test.describe("React · public access and smoke", () => {
  test("loads without sign-in and shows Start with the real scenarios", async ({ page, request }) => {
    const { response, errors } = await open(page, "/");
    expect(response?.status()).toBe(200);
    expect((await request.get("/api/healthz")).ok()).toBe(true);
    await expect(page.getByRole("heading", { name: /Turn enterprise knowledge into decisions and outcomes/ })).toBeVisible();
    for (const id of ["go-live", "sec-217", "uat-perf"]) await expect(tid(page, `scenario-${id}`)).toBeVisible();
    await expect(tid(page, "analyze-with-workpilot")).toBeEnabled();
    // Only steps with recorded work open; everything after Start is locked on a fresh visit.
    await expect(tid(page, "stepper-knowledge")).toBeDisabled();
    await expect(tid(page, "stepper-outcome")).toBeDisabled();
    assertNoErrors(errors);
  });

  test("sidebar shows only items with real content", async ({ page }) => {
    await open(page, "/");
    for (const id of ["nav-new", "nav-sessions", "nav-memory", "nav-status"]) await expect(tid(page, id)).toBeVisible();
    const nav = await page.locator("aside nav").innerText();
    expect(nav).not.toMatch(/Analytics|Enterprise Scenarios/);
  });

  test("an unknown session link says so instead of inventing one", async ({ page }) => {
    await open(page, "/?s=SES-doesnotexist");
    await expect(tid(page, "error")).toContainText(/not found/i);
  });
});
