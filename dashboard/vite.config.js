import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the API is reached through Vite's proxy at /api, as nginx does in the container,
// so the dashboard and the API share an origin and need no CORS.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        ws: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
