import { defineConfig } from "vitest/config";

export default defineConfig({
	test: {
		// tests/manual holds scripts that launch a real browser; they are run by
		// hand (pnpm smoke), never by the suite.
		include: ["tests/*.test.ts"],
		testTimeout: 30_000,
		hookTimeout: 30_000,
	},
});
