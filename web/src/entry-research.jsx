import { StrictMode, Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
import "./theme.css";
import "./editorial.css";
import { installCacheGuard } from "./lib/cache-guard.js";

const Research = import.meta.env.VITE_SHOW_PICK_METHOD === "false" ? null : lazy(() => import("./pages/Research.jsx"));
installCacheGuard();
createRoot(document.getElementById("root")).render(<StrictMode>
  {Research ? <Suspense fallback={<p>불러오는 중…</p>}><Research /></Suspense> : <main><p>이 페이지는 제공되지 않습니다.</p><a href="markets.html">경기 분석으로 이동</a></main>}
</StrictMode>);
