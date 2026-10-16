import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Console v2 is served by archivist.webapp at /console/ (DESIGN-BRIEF §2).
export default defineConfig({
  base: "/console/",
  plugins: [react()],
  build: {
    outDir: "dist",
    target: "es2022",
    sourcemap: false,
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
