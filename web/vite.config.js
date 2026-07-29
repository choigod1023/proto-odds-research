import { resolve } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwind from "@tailwindcss/vite";

// ⚠️ 산출물은 ../docs 로 나간다. GitHub Pages 가 main:/docs 를 그대로 서빙하기 때문이다.
//    **emptyOutDir 는 반드시 false** — true 면 수집기가 6시간마다 채우는
//    docs/data/*.json 과 docs/사전조사_원본.md 가 빌드할 때마다 지워진다.
//
//    멀티페이지로 두는 이유: SPA 라우팅을 쓰면 /markets.html 같은 기존 URL 이
//    깨지고 Pages(legacy) 에서 404 폴백을 따로 만들어야 한다. 엔트리를 그대로
//    유지하면 URL·Pages 설정을 하나도 안 바꿔도 된다.
//
//    아직 React 로 안 옮긴 페이지는 docs/ 에 그대로 남아 계속 서빙된다.
//    (엔트리에 없는 파일은 빌드가 건드리지 않는다)
export default defineConfig({
  root: __dirname,
  base: "./", // 서브패스(/proto-odds-research/)에서 서빙되므로 상대 경로로
  plugins: [react(), tailwind()],
  build: {
    outDir: resolve(__dirname, "../docs"),
    emptyOutDir: false,
    rollupOptions: {
      input: {
        index: resolve(__dirname, "index.html"),
        markets: resolve(__dirname, "markets.html"),
        research: resolve(__dirname, "research.html"),
      },
    },
  },
});
