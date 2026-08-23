import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import Lotto from "./pages/Lotto.jsx";
import "./theme.css";
import "./editorial.css";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <Lotto />
  </StrictMode>,
);
