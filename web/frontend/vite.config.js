import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, proxy /api to the FastAPI backend so the frontend can use relative
// URLs that also work when the built app is served from FastAPI (frontend/dist).
export default defineConfig({
  plugins: [react()],
  server: {
    // localhost only by default. The dev server is NOT a hardened server (the
    // esbuild advisory GHSA-67mh-4wv8-2f99, fixed by Vite 8, let any page you
    // visit read its responses), and this one proxies /api straight to the
    // backend. On a shared machine binding 0.0.0.0 would expose both to everyone
    // on the network. Set VITE_DEV_HOST=0.0.0.0 deliberately if you need to reach it
    // from another machine (use an SSH tunnel in preference).
    host: process.env.VITE_DEV_HOST || "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET || "http://localhost:8700",
        changeOrigin: true,
      },
    },
  },
  build: { outDir: "dist" },
});
