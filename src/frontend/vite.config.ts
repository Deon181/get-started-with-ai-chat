import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "~": resolve(__dirname, "./src"),
    },
  },
  build: {
    outDir: "../api/static/react",
    emptyOutDir: true,
    manifest: true,
    cssCodeSplit: false,
    rollupOptions: {
      input: {
        main: "./src/main.tsx",
      },
    },
  },
});
