// Keep the existing endpoint until the independent web app is verified.
export const API_ORIGIN = (import.meta.env?.VITE_MATCH_API_ORIGIN
  || 'https://proto-odds-collector.fly.dev').replace(/\/+$/, '');
export const apiUrl = path => `${API_ORIGIN}${path}`;
