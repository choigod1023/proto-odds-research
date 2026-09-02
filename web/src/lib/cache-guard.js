const ASSET_PATTERN = /(?:src|href)=["'][^"']*\/assets\/([^"'?]+\.(?:js|css))(?:\?[^"']*)?["']/gi;

export function assetSignatureFromHtml(html = "") {
  return [...String(html).matchAll(ASSET_PATTERN)]
    .map((match) => match[1])
    .sort()
    .join("|");
}

const freshUrl = () => {
  const url = new URL(window.location.href);
  url.searchParams.set("_fresh", Date.now());
  return url;
};

/** 모바일 BFCache와 GitHub Pages의 오래된 진입 HTML을 새 배포 자산으로 교체한다. */
export function installCacheGuard({ minimumIntervalMs = 30000 } = {}) {
  let lastChecked = 0;
  let checking = false;
  const check = async () => {
    const now = Date.now();
    if (checking || now - lastChecked < minimumIntervalMs) return false;
    checking = true;
    lastChecked = now;
    try {
      const response = await fetch(freshUrl(), {
        cache: "no-store",
        headers: { "Cache-Control": "no-cache" },
      });
      if (!response.ok) return false;
      const latest = assetSignatureFromHtml(await response.text());
      const current = assetSignatureFromHtml(document.documentElement.outerHTML);
      if (latest && current && latest !== current) {
        const reload = freshUrl();
        reload.searchParams.set("_build", latest.slice(0, 80));
        window.location.replace(reload.href);
        return true;
      }
    } catch {
      // 오프라인·일시 장애에서는 현재 화면을 유지하고 다음 focus 때 다시 확인한다.
    } finally {
      checking = false;
    }
    return false;
  };
  window.addEventListener("pageshow", check);
  window.addEventListener("focus", check);
  window.addEventListener("online", check);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") check();
  });
  check();
  return check;
}
