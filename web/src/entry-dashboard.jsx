import React from "react";
import { createRoot } from "react-dom/client";
import Dashboard from "./pages/Dashboard.jsx";

import "./theme.css";
import "./editorial.css";
import { installCacheGuard } from "./lib/cache-guard.js";

installCacheGuard();

createRoot(document.getElementById("root")).render(<Dashboard />);
