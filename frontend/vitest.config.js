import { defineConfig, mergeConfig } from "vitest/config";
import viteConfig from "./vite.config.js";

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: "jsdom",
      // React only exposes act() in its development build; some shells
      // export NODE_ENV=production, which would load the production build.
      env: { NODE_ENV: "test" },
    },
  })
);
