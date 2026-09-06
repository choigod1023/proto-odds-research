// Explicit local paths only. Does not upload images or save private OCR content.
// node scripts/check-receipt-ocr.mjs path/to/receipt.png [...]
import { readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename } from "node:path";
import assert from "node:assert/strict";
import { PNG } from "pngjs";
import { createWorker } from "tesseract.js";
import { scanReceiptImage } from "../src/lib/receipt-scan.js";
import {
  receiptDrafts,
  scannedTicketSummary,
} from "../src/lib/receipt-draft.js";

const args = process.argv.slice(2);
const expected = args.find((arg) => arg.startsWith("--expect="));
const variants = args.includes("--variants")
  ? ["original", "padding", "double"]
  : [
      args.find((arg) => arg.startsWith("--variant="))?.split("=")[1] ||
        "original",
    ];
const worker = await createWorker("kor+eng", 1, { cachePath: tmpdir() });
try {
  for (const path of args.filter((arg) => !arg.startsWith("--")))
    for (const variant of variants) {
      const result = await scanReceiptImage(path, {
        worker,
        decode: async (file) => {
          const source = PNG.sync.read(await readFile(file));
          if (variant === "original") return source;
          const factor = variant === "double" ? 2 : 1,
            pad = variant === "padding" ? 180 : 0;
          const target = new PNG({
            width: source.width * factor + pad * 2,
            height: source.height * factor + pad * 2,
          });
          target.data.fill(255);
          for (let y = 0; y < source.height * factor; y++)
            for (let x = 0; x < source.width * factor; x++) {
              const p =
                (Math.floor(y / factor) * source.width +
                  Math.floor(x / factor)) *
                4;
              target.data.set(
                source.data.subarray(p, p + 4),
                ((y + pad) * target.width + x + pad) * 4,
              );
            }
          return target;
        },
        encode: (image) => PNG.sync.write(image),
      });
      const drafts = receiptDrafts(result).map(
        ({ gameNo, home, away, md, market, line, choice, purchaseOdds }) => ({
          gameNo,
          home,
          away,
          md,
          market,
          line,
          choice,
          purchaseOdds,
        }),
      );
      const ticket = scannedTicketSummary(result);
      if (args.includes("--debug")) console.log(JSON.stringify(result.rows));
      if (expected) {
        const contract = JSON.parse(expected.slice("--expect=".length));
        assert.equal(drafts.length, contract.rows.length);
        contract.rows.forEach((row, index) =>
          Object.entries(row).forEach(([key, value]) =>
            assert.deepEqual(
              drafts[index][key],
              value,
              `${variant} row ${index + 1} ${key}`,
            ),
          ),
        );
        Object.entries(contract.ticket || {}).forEach(([key, value]) =>
          assert.deepEqual(ticket[key], value, `${variant} ticket ${key}`),
        );
      }
      console.log(
        JSON.stringify(
          { file: basename(path), variant, drafts, ticket },
          null,
          2,
        ),
      );
    }
} finally {
  await worker.terminate();
}
