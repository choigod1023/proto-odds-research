import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import Prices from "./pages/Prices.jsx";
import "./theme.css";
import "./editorial.css";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <Prices />
  </StrictMode>,
);
