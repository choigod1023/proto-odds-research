import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import Terms from './pages/Terms.jsx';
import './theme.css';import './editorial.css';
import { installCacheGuard } from './lib/cache-guard.js';
installCacheGuard();
createRoot(document.getElementById('root')).render(<StrictMode><Terms/></StrictMode>);
