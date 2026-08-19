import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const root = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  plugins: [react()],
  build: {
    lib: {
      entry: {
        "supplier-onboarding": resolve(
          root,
          "src/features/supplier-onboarding/index.ts",
        ),
        "supplier-profile-review": resolve(
          root,
          "src/features/supplier-profile-review/index.ts",
        ),
      },
      formats: ["es"],
    },
    rollupOptions: {
      external: [
        "react",
        "react-dom",
        "react/jsx-runtime",
        "react-router-dom",
      ],
    },
  },
});
