import { expect, test } from "@playwright/test";
import { analyze, ask, prepareContracts, step, tid } from "./helpers";

test.describe("React · phone width", () => {
  test.use({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });

  test("every step fits a 375px screen with no sideways scroll", async ({ page }) => {
    await ask(page, { scenario: "go-live" });
    const fits = async () => {
      const [scroll, width] = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth])
      expect(scroll, "page scrolls sideways").toBeLessThanOrEqual(width);
    };
    await fits();
    await analyze(page);
    await fits();
    await prepareContracts(page);
    for (const id of ["actions", "decision", "approval", "outcome"]) {
      await page.evaluate(() => window.scrollTo(0, 0));
      await tid(page, `stepper-${id}`).scrollIntoViewIfNeeded();
      await step(page, id);
      await fits();
    }
    await expect(page.locator(".stepper .current .lbl")).toBeVisible();
    await page.getByRole("button", { name: "Open navigation" }).click();
    await expect(tid(page, "nav-status")).toBeVisible();
  });
});
