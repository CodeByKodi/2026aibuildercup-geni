import path from "node:path";
import { expect, test } from "@playwright/test";
import { open, tid } from "./helpers";

const SCANNED = path.resolve(__dirname, "../../sample_data/scanned/cab_minutes_scanned.pdf");

test.describe("React · document upload", () => {
  test("a scanned PDF is read by Document AI, kept with the session, and searched", async ({ page, request }) => {
    const status = await (await request.get("/api/status")).json();
    const docai = status.rows.find((r: { technology: string }) => r.technology === "Document AI");
    test.skip(docai?.status !== "ACTIVE", "Document AI is not live on this deployment; an image-only PDF needs OCR");
    await open(page, "/");
    await tid(page, "upload-input").setInputFiles(SCANNED);
    const row = tid(page, "upload-row");
    await expect(row).toContainText("cab_minutes_scanned.pdf", { timeout: 120_000 });
    // An image-only PDF has no text layer: only OCR can read it.
    await expect(row).toContainText("read by Google Document AI");
    await expect(row).toContainText(/PDF · [\d.]+ KB · \d+ words/);

    await tid(page, "question").fill("Who owns the CAB-77 rollback rehearsal and how long did the rollback take?");
    await tid(page, "analyze-with-workpilot").click();
    await expect(tid(page, "step-knowledge")).toBeVisible({ timeout: 90_000 });
    const card = tid(page, "source-card").filter({ hasText: "cab_minutes_scanned.pdf" });
    await expect(card).toContainText("Uploaded by you");
    await expect(card).toContainText(/Cited · \d+/);
    await expect(tid(page, "retrieval-mode")).toContainText(/from your uploads/);

    // Survives a reload: it is stored with the session in Firestore.
    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(tid(page, "source-card").filter({ hasText: "cab_minutes_scanned.pdf" })).toBeVisible({ timeout: 60_000 });
  });
});
