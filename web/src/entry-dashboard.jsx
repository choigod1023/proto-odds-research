import React from "react";
import { createRoot } from "react-dom/client";
import Dashboard from "./pages/Dashboard.jsx";
import { ThemeToggle } from "./components/ui.jsx";
import "./theme.css";

createRoot(document.getElementById("root")).render(<><Dashboard /><ThemeToggle /></>);
