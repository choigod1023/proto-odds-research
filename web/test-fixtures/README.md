# Receipt OCR fixtures

`receipt-choice-strokes.png` is a synthetic receipt with fictional teams, game
number, date and odds. Only the generic blue `승` glyph and its immediate blank
background were isolated from a failing UI sample; no user's ticket, purchase
amount, identifiers or original full image is included.

Before the ink-bound crop fix, the engine read the label as `스 / ㄷ`, leaving the
choice empty. `npm run test:ocr` runs the actual Korean/English Tesseract pipeline
on the original fixture, a padded copy, and a double-resolution copy. It checks
the recovered choice and printed numeric fields, and verifies that missing
ticket totals remain missing. It does not assert recovery of every team name.

The first run downloads Tesseract language data to the OS temporary cache. The
fixture stays local and is not uploaded. CI runs this regression before building.
