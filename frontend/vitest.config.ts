import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/features/**/__tests__/**/*.test.ts?(x)"],
    coverage: {
      provider: "v8",
      include: ["src/features/**/*.{ts,tsx}"],
      exclude: [
        "src/features/**/__tests__/**",
        "src/features/**/index.ts",
        "src/features/**/*.d.ts",
      ],
      reporter: ["text", "json-summary"],
    },
  },
});
