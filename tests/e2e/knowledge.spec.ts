import { expect, test } from "@playwright/test";
import { ask, tid } from "./helpers";

test.describe("React · knowledge sources", () => {
  test("source cards show real file metadata and what the search cited", async ({ page }) => {
    await ask(page, { scenario: "go-live" });
    const cards = tid(page, "source-card");
    await expect(cards).toHaveCount(6);
    const text = await tid(page, "source-cards").innerText();
    expect(text).toMatch(/Markdown · [\d,]+ words · [\d.]+ KB/);
    expect(text).not.toMatch(/PDF ·|pages|items|days ago/); // the mockup's placeholder metadata never appears
    expect(text).toContain("issue_tracker.md");
    expect(await page.getByText(/Cited · \d+/).count()).toBeGreaterThan(0);
    await expect(tid(page, "retrieval-mode")).toContainText(/returned \d+ passages from \d+ of 6 sources/);
  });
});
