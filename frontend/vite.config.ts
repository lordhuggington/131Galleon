import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// The Python app serves ../static, so the production build writes straight into it.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../static", emptyOutDir: true },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/photos": "http://localhost:8000",
      "/healthz": "http://localhost:8000",
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
