# Receipt image recognition

The dashboard processes screenshots locally with Tesseract. Images and OCR text
are not uploaded; saving a reviewed ticket writes to the existing device ledger.
Model/worker downloads may contact the OCR dependency CDN. OCR imports no longer
automatically submit receipt selections to anonymous statistics.

## Input and recognition

- Desktop: focus the paste/drop area and use Ctrl+V / Command+V, drag one image,
  choose a file, or explicitly press the clipboard button.
- Mobile: separate album and rear-camera controls; clipboard reading requires
  browser support, a secure context, user activation, and browser permission.
  Unsupported clipboard access falls back to file selection. HEIC requires a
  screenshot or JPEG conversion. File limit: 20 MiB; decoded limit: 24 MP.
- Detect connected saturated-blue selected cells (light outline or dark winning
  fill), reject header noise, normalize against cell height and table bounds,
  read each selected button and its own row separately, and read green total-line
  regions separately. No choice is inferred from absolute screen position.
- Preserve printed individual odds and the separately printed combined odds.
  A summary is not reconstructed from current prices. Missing values stay blank.
- Game number alone cannot connect historical receipts to the current feed.
  Optional matching requires year, day, both teams, number, market and line.
  User-corrected receipt records never inherit today's opening probabilities.
- The user reviews every leg and the ticket, supplies missing year/purchase date,
  and corrects OCR before saving. The whole ticket is written once. Cross-year
  live-result matching is blocked for records carrying an explicit year.

## Reproducible local checks

Run `npm ci` in `web`, then:

```sh
node scripts/check-receipt-ocr.mjs /absolute/path/to/receipt.png --variants
```

This runs the same pixel/OCR pipeline as the browser with a PNG adapter. Variants
include the original, 180-pixel surrounding padding, and a 2x image. Optional
`--expect=<JSON>` asserts fields in `{ "rows": [...], "ticket": {...} }`;
`--debug` explicitly prints the per-region raw OCR text. Treat output as private.
Do not commit real receipts, purchase identifiers, or account screenshots.

September 6, 2026 acceptance checks:

- Two supplied modern layouts, each original/padded/2x: game numbers, selections,
  individual prices, U/O line, leg count, stake, combined odds and payout passed.
- One supplied older compact layout: both game numbers/selections/prices, U/O
  line and ticket totals passed. The tiny mixed Latin/Korean team names still
  need manual correction; these checks do **not** claim perfect team-name OCR.
- One newly captured completed Betman winning ticket: three game numbers,
  selections/prices, handicap line and ticket totals passed. Some team strings
  still need correction. The site's winning label was verified visually, not
  promoted to an official settlement from OCR.
- Responsive input UI checked in Chrome at 390px without horizontal overflow.
  Browser file injection was blocked by the extension's file-URL permission.
  Real clipboard paste, real phone camera/album and on-device OCR remain manual
  acceptance checks, not claimed as completed end-to-end tests.

## Limits

This is a reviewable input aid, not a guarantee of perfect OCR. Blurred text,
black-and-white or strongly rotated/perspective photographs, unsupported markets,
very narrow source text, and multiple selected boxes on the same row require a
cleaner crop or manual correction. More than 40 detected boxes is rejected before
OCR. No automatic save, purchase, official result override, or server restart is
part of this workflow. Historical result retrieval is separate from recognition.
