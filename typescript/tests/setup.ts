/**
 * Every test file runs with telemetry off (a test with a fake sender turns it
 * back on) and without the user's own defaults file. Twin of the
 * isolated_environment fixture in python/tests/conftest.py.
 */
import * as os from "node:os";
import * as path from "node:path";

process.env.CAMOUWOLF_TELEMETRY = "0";
delete process.env.DO_NOT_TRACK;
process.env.CAMOUWOLF_DEFAULTS = path.join(
	os.tmpdir(),
	`camouwolf-no-user-defaults-${process.pid}`,
	"defaults.json",
);
