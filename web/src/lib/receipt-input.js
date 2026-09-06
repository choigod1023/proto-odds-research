export const MAX_RECEIPT_IMAGE_BYTES = 20 * 1024 * 1024;
export const RECEIPT_IMAGE_TYPES = Object.freeze([
  "image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp", "image/avif",
]);
export const RECEIPT_IMAGE_ACCEPT = RECEIPT_IMAGE_TYPES.join(",");
export const CLIPBOARD_FALLBACK = "붙여넣기 영역을 선택하고 Ctrl+V 또는 ⌘V를 누르거나 사진 선택을 이용해 주세요.";

// Metadata validation only: decoding and pixel/dimension limits belong to the OCR caller.
// Do not trust filename extensions or silently accept an absent MIME type.
export function validateReceiptImage(file) {
  if (!file) return { ok: false, code: "missing", message: "이미지 한 장을 선택해 주세요." };
  if (!RECEIPT_IMAGE_TYPES.includes(String(file.type || "").toLowerCase())) {
    return { ok: false, code: "type", message: "PNG, JPEG, WebP, GIF, BMP, AVIF 이미지를 선택해 주세요. HEIC/HEIF 사진은 JPEG로 변환하거나 화면을 캡처해 주세요." };
  }
  if (!Number.isSafeInteger(file.size) || file.size <= 0) {
    return { ok: false, code: "empty", message: "이미지가 비어 있거나 파일 크기를 확인할 수 없습니다. 다른 사진을 선택해 주세요." };
  }
  if (file.size > MAX_RECEIPT_IMAGE_BYTES) {
    return { ok: false, code: "size", message: "이미지는 한 장당 20 MiB 이하로 선택해 주세요. 필요한 영역만 잘라 다시 시도해 주세요." };
  }
  return { ok: true, code: null, message: "" };
}

// DataTransfer.files may be empty for pasted images; items is the fallback.
// Ignore accompanying HTML/text, and let validation explain non-image files.
export function receiptFilesFromTransfer(transfer) {
  const files = Array.from(transfer?.files || []);
  if (files.length) return files;
  return Array.from(transfer?.items || [])
    .filter((item) => item.kind === "file")
    .map((item) => item.getAsFile?.())
    .filter(Boolean);
}

// Call only from the explicit clipboard button's click handler. Never read on mount.
export async function readReceiptClipboard(clipboard) {
  if (typeof clipboard?.read !== "function") {
    throw new Error(`이 브라우저에서는 클립보드 버튼을 사용할 수 없습니다. ${CLIPBOARD_FALLBACK}`);
  }
  let items;
  try {
    items = await clipboard.read();
  } catch {
    throw new Error(`클립보드를 읽지 못했습니다. 브라우저 권한과 보안 연결(HTTPS)을 확인해 주세요. ${CLIPBOARD_FALLBACK}`);
  }
  const images = Array.from(items || []).filter((item) =>
    Array.from(item.types || []).some((type) => type.startsWith("image/")));
  if (!images.length) throw new Error(`클립보드에 이미지가 없습니다. ${CLIPBOARD_FALLBACK}`);
  if (images.length > 1) throw new Error("이미지는 한 번에 한 장씩 붙여넣어 주세요.");
  const item = images[0];
  const type = RECEIPT_IMAGE_TYPES.find((candidate) => item.types.includes(candidate));
  if (!type) throw new Error(validateReceiptImage({ type: item.types.find((value) => value.startsWith("image/")) }).message);
  try {
    const blob = await item.getType(type);
    const extension = type === "image/jpeg" ? "jpg" : type.slice(6);
    return new File([blob], `clipboard.${extension}`, { type });
  } catch {
    throw new Error(`클립보드 이미지를 열지 못했습니다. ${CLIPBOARD_FALLBACK}`);
  }
}
