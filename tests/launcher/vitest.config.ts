import { defineConfig } from "vitest/config";

export default defineConfig({
	test: {
		include: ["tests/launcher/**/*.test.ts"],
		// These are pure-logic tests over the identity model — no browser, no
		// network. The browser-driving checks live in tests/compare/ and are run
		// by tests/ci/run-heavy-suite.sh, because they need a built binary and
		// take minutes rather than milliseconds.
		environment: "node",
		testTimeout: 20_000,
		root: new URL("../..", import.meta.url).pathname,
	},
});
