import test from "node:test";
import assert from "node:assert/strict";
import { scanReceiptImage } from "./receipt-scan.js";
import { receiptDrafts } from "./receipt-draft.js";

function raster(rects, width = 600, height = 1000) {
  const data = new Uint8ClampedArray(width * height * 4).fill(255);
  for (const { x, y, w = 80, h = 24 } of rects) {
    for (let yy = y; yy < y + h; yy++)
      for (let xx = x; xx < x + w; xx++) {
        if (xx === x || xx === x + w - 1 || yy === y || yy === y + h - 1)
          data.set([0, 140, 255, 255], (yy * width + xx) * 4);
      }
    for (let yy = y + 4; yy < y + 11; yy++)
      for (let xx = x + Math.floor(w / 2); xx < x + Math.floor(w / 2) + 4; xx++)
        data.set([0, 110, 190, 255], (yy * width + xx) * 4);
  }
  return { data, width, height };
}

test("table-level layout evidence keeps a misread row's team crop above its button", async () => {
  const images = [];
  const texts = ["조합 한경기", "승 1.65", "SNORE 야구 승패", "MLB", "4911", "북부 구단 vs 남부 구단"];
  const result = await scanReceiptImage(null, {
    decode: async () => raster([{ x: 380, y: 100 }], 600, 200),
    encode: (image) => image,
    worker: {
      setParameters: async () => {},
      recognize: async (image) => {
        images.push(image);
        return { data: { text: texts[images.length - 1] || "" } };
      },
    },
  });
  assert.equal(result.rows[0].teamText, "북부 구단 vs 남부 구단");
  // 61% of the 600px table, scaled 4x, plus 24px OCR padding.
  assert.equal(images[5].width, 1488);
});

test("unreadable selection retries tightly cropped ink at two scales without changing odds", async () => {
  for (const [label, choice] of [["승", "홈"], ["패", "원정"], ["무", "무"], ["언더", "언더"], ["오버", "오버"]]) {
    const modes = [], images = [];
    const texts = ["조합", "스\n[=]\n1.65", label, `${label}\n1.85`, "야구 승패", "", "4911", "북부 구단 vs 남부 구단"];
    const result = await scanReceiptImage(null, {
      decode: async () => raster([{ x: 380, y: 100 }], 600, 200),
      encode: (image) => image,
      worker: {
        setParameters: async (params) => modes.push(params.tessedit_pageseg_mode),
        recognize: async (image) => {
          images.push(image);
          return { data: { text: texts.shift() || "", confidence: 90 } };
        },
      },
    });
    const [draft] = receiptDrafts(result);
    assert.equal(draft.choice, choice);
    assert.equal(draft.purchaseOdds, 1.65);
    assert.equal(draft.reviewed, false);
    assert.ok(draft.buttonText.startsWith("스\n[=]\n1.65"));
    assert.ok(images[2].height < images[3].height);
    assert.ok(images[3].width < images[1].width);
    assert.ok(images[3].width <= 1224 && images[3].height <= 384);
    assert.deepEqual(modes.slice(0, 4), ["6", "6", "7", "6"]);
  }
});

test("retry disagreement or missing labels never guesses a selection from row text", async () => {
  for (const retries of [["승", "패"], ["승", "스"], ["", "승"], ["승 패", "승"]]) {
    const texts = ["조합", "스\n[=]\n1.65", ...retries, "승 1.65 패 2.10", "", "4911", "북부 구단 vs 남부 구단"];
    const result = await scanReceiptImage(null, {
      decode: async () => raster([{ x: 380, y: 100 }], 600, 200),
      encode: (image) => image,
      worker: {
        setParameters: async () => {},
        recognize: async () => ({ data: { text: texts.shift() || "", confidence: 90 } }),
      },
    });
    assert.equal(receiptDrafts(result)[0].choice, "");
    assert.equal(result.rows[0].buttonText, "스\n[=]\n1.65");
  }
});

test("readable and conflicting choices do not trigger label retries", async () => {
  for (const [button, expected] of [["승 1.65", "홈"], ["패 1.65", "원정"], ["승 패 1.65", ""]]) {
    const texts = ["조합", button, "야구 승패", "", "4911", "북부 구단 vs 남부 구단"];
    let calls = 0;
    const result = await scanReceiptImage(null, {
      decode: async () => raster([{ x: 380, y: 100 }], 600, 200),
      encode: (image) => image,
      worker: {
        setParameters: async () => {},
        recognize: async () => { calls++; return { data: { text: texts.shift() || "" } }; },
      },
    });
    assert.equal(calls, 6);
    assert.equal(receiptDrafts(result)[0].choice, expected);
  }
});

test("two low-confidence reads do not turn noise into a selected choice", async () => {
  for (const confidence of [0, 69, undefined]) {
    const texts = ["조합", "스 1.65", "승", "승", "야구 승패", "", "4911", "북부 구단 vs 남부 구단"];
    const result = await scanReceiptImage(null, {
      decode: async () => raster([{ x: 380, y: 100 }], 600, 200),
      encode: (image) => image,
      worker: {
        setParameters: async () => {},
        recognize: async () => ({ data: { text: texts.shift() || "", confidence } }),
      },
    });
    assert.equal(receiptDrafts(result)[0].choice, "");
  }
});
test("excessive candidate boxes stop before OCR calls", async () => {
  let calls = 0;
  await assert.rejects(
    scanReceiptImage(null, {
      decode: async () =>
        raster(
          Array.from({ length: 50 }, (_, i) => ({ x: 100, y: i * 35 })),
          600,
          2000,
        ),
      encode: async (v) => v,
      worker: {
        recognize: () => {
          calls++;
        },
      },
    }),
    /너무 많/,
  );
  assert.equal(calls, 0);
});
test("same-row multiple selections fail closed instead of pairing another row's identity", async () => {
  await assert.rejects(
    scanReceiptImage(null, {
      decode: async () =>
        raster([
          { x: 100, y: 60 },
          { x: 300, y: 60 },
        ]),
      worker: {},
      encode: (v) => v,
    }),
    /같은 행/,
  );
});
test("oversized decoded images stop before allocating selection masks", async () => {
  await assert.rejects(
    scanReceiptImage(null, {
      decode: async () => ({ width: 10000, height: 10000, data: [] }),
      worker: {},
    }),
    /화소/,
  );
});
test("no colored selection does not fall back to current odds or screen position", async () => {
  const result = await scanReceiptImage(null, {
    decode: async () => raster([], 100, 100),
    encode: (v) => v,
    worker: {
      setParameters: async () => {},
      recognize: async () => ({ data: { text: "게임번호 9 승 1.91" } }),
    },
  });
  assert.deepEqual(result.rows, []);
});
