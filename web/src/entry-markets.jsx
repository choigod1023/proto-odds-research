import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import Markets from "./pages/Markets.jsx";
import "./theme.css";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <Markets />
  </StrictMode>,
);
