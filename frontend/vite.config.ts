import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const normalizeBasePath = (value: string): string => {
  let base = value.trim();
  if (!base.startsWith("/")) {
    base = `/${base}`;
  }
  if (!base.endsWith("/")) {
    base = `${base}/`;
  }
  return base;
};

const repoName = process.env.GITHUB_REPOSITORY?.split("/")[1] ?? "";
const defaultPagesBase = repoName ? `/${repoName}/` : "/";
const configuredBase = process.env.VITE_BASE_PATH?.trim();
const base = normalizeBasePath(configuredBase || defaultPagesBase);

export default defineConfig({
  base,
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
  },
});
