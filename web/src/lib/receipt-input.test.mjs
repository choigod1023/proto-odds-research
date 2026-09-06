import test from "node:test";
import assert from "node:assert/strict";
import {
  MAX_RECEIPT_IMAGE_BYTES, RECEIPT_IMAGE_ACCEPT, RECEIPT_IMAGE_TYPES,
  readReceiptClipboard, receiptFilesFromTransfer, validateReceiptImage,
} from "./receipt-input.js";

const file = (type = "image/png", size = 100) => ({ type, size, name: "receipt.png" });
const clipboardItem = (types, getType = async (type) => new Blob(["image"], { type })) => ({ types, getType });

test("supported raster MIME types and exact maximum size are accepted", () => {
  for (const type of RECEIPT_IMAGE_TYPES) {
    assert.equal(validateReceiptImage(file(type, 1)).ok, true);
    assert.equal(validateReceiptImage(file(type, MAX_RECEIPT_IMAGE_BYTES)).ok, true);
    assert.ok(RECEIPT_IMAGE_ACCEPT.includes(type));
  }
  assert.equal(validateReceiptImage(file("IMAGE/JPEG")).ok, true);
});

test("missing files, empty/unknown MIME and misleading image extensions are rejected", () => {
  assert.equal(validateReceiptImage(null).code, "missing");
  assert.equal(validateReceiptImage(undefined).code, "missing");
  for (const type of ["", undefined, "text/plain", "application/pdf", "image/svg+xml", "image/heic", "image/heif", "image/tiff", "image/unknown"]) {
    assert.equal(validateReceiptImage({ ...file(), type }).code, "type");
  }
  assert.equal(validateReceiptImage({ ...file(), name: "no-extension" }).ok, true);
});

test("empty, malformed, and oversized sizes fail without coercion", () => {
  for (const size of [0, -1, NaN, Infinity, undefined, "100", 1.5]) {
    assert.equal(validateReceiptImage({ ...file(), size }).code, "empty");
  }
  assert.equal(validateReceiptImage({ type: "image/png" }).code, "empty");
  assert.equal(validateReceiptImage(file("image/png", MAX_RECEIPT_IMAGE_BYTES + 1)).code, "size");
});

test("real File metadata is validated and the original object is not modified", () => {
  const image = new File(["image"], "receipt.png", { type: "image/png" });
  assert.equal(validateReceiptImage(image).ok, true);
  assert.equal(image.size, 5);
});

test("transfer uses files once, retaining multiple files for the UI to reject", () => {
  const first = file(); const second = file("image/jpeg");
  assert.deepEqual(receiptFilesFromTransfer({ files: [first, second], items: [{ kind: "file", getAsFile: () => first }] }), [first, second]);
});

test("paste items fallback ignores HTML/text and null files", () => {
  const image = file();
  assert.deepEqual(receiptFilesFromTransfer({ files: [], items: [
    { kind: "string", type: "text/html" }, { kind: "file", getAsFile: () => null },
    { kind: "file", getAsFile: () => image },
  ] }), [image]);
  assert.deepEqual(receiptFilesFromTransfer(undefined), []);
});

test("clipboard read is called immediately and image representations are not duplicate files", async () => {
  let read = false; let chosen;
  const pending = readReceiptClipboard({ read: () => {
    read = true;
    return Promise.resolve([clipboardItem(["text/html", "image/jpeg", "image/png"], async (type) => {
      chosen = type; return new Blob(["pixels"], { type });
    })]);
  } });
  assert.equal(read, true);
  const image = await pending;
  assert.ok(image instanceof File);
  assert.equal(chosen, "image/png");
  assert.equal(image.type, "image/png");
  assert.equal(image.name, "clipboard.png");
  assert.equal(await image.text(), "pixels");
});

test("clipboard JPEG gets a usable filename and accompanying text is ignored", async () => {
  const image = await readReceiptClipboard({ read: async () => [clipboardItem(["text/plain"]), clipboardItem(["image/jpeg"])] });
  assert.equal(image.name, "clipboard.jpg");
});

test("clipboard API unavailable and permission rejection offer keyboard/file fallback", async () => {
  for (const clipboard of [undefined, {}, { read: async () => { throw new Error("denied"); } }]) {
    await assert.rejects(readReceiptClipboard(clipboard), /Ctrl\+V.*사진 선택/);
  }
});

test("clipboard without an image, multiple images, and unsupported image fail clearly", async () => {
  await assert.rejects(readReceiptClipboard({ read: async () => [clipboardItem(["text/html"])] }), /이미지가 없습니다/);
  await assert.rejects(readReceiptClipboard({ read: async () => [clipboardItem(["image/png"]), clipboardItem(["image/jpeg"])] }), /한 번에 한 장/);
  await assert.rejects(readReceiptClipboard({ read: async () => [clipboardItem(["image/heic"])] }), /HEIC\/HEIF/);
});

test("clipboard decoding failure provides fallback and empty blobs still fail validation", async () => {
  await assert.rejects(readReceiptClipboard({ read: async () => [clipboardItem(["image/png"], async () => { throw new Error("unreadable"); })] }), /Ctrl\+V/);
  const empty = await readReceiptClipboard({ read: async () => [clipboardItem(["image/png"], async () => new Blob([]))] });
  assert.equal(validateReceiptImage(empty).code, "empty");
});
