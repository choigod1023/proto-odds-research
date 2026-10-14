import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

// Real Tesseract regression, kept separate from fast/mock unit tests.
const fixture = fileURLToPath(new URL("../test-fixtures/receipt-choice-strokes.png", import.meta.url));
const expected = {
  rows: [{ gameNo: "4911", md: "09.06", choice: "홈", purchaseOdds: 1.8, market: "승패", period: "" }],
  ticket: { legCount: null, combinedOdds: "", stake: "", expectedPayout: "" },
};
const result = spawnSync(process.execPath, [
  fileURLToPath(new URL("./check-receipt-ocr.mjs", import.meta.url)), fixture,
  "--variants", `--expect=${JSON.stringify(expected)}`,
], { stdio: "inherit", timeout: 120000 });
if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
