import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import Slip from "./pages/Slip.jsx";
import "./theme.css";
import "./editorial.css";

createRoot(document.getElementById("root")).render(
  <StrictMode><Slip /></StrictMode>,
);
