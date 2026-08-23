import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import Research from "./pages/Research.jsx";
import "./theme.css";
import "./editorial.css";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <Research />
  </StrictMode>,
);
