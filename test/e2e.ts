/**
 * End-to-end check without the TUI: starts pi in RPC mode, lets this extension
 * abort a real run and then call the same function `/retry` calls.
 *
 * Drive it with test/e2e-driver.py.
 */
import { appendFileSync } from "node:fs";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import piRetry, { retry } from "../index.ts";

const LOG = process.env.PI_RETRY_E2E_LOG ?? "/tmp/pi-retry-e2e.log";
const log = (message: string) => appendFileSync(LOG, `${message}\n`);

export default function (pi: ExtensionAPI) {
	piRetry(pi);

	let phase: "idle" | "aborting" | "retrying" | "done" = "idle";

	pi.on("agent_settled", async (event, ctx) => {
		log(`settled aborted=${event.aborted} phase=${phase}`);
		if (phase === "aborting") {
			phase = "retrying";
			await retry(pi, ctx as never, process.env.PI_RETRY_ARGS ?? "");
			log("retry() returned");
			return;
		}
		if (phase === "retrying") {
			phase = "done";
			log("retried run settled");
		}
	});

	pi.on("input", (event, ctx) => {
		if (phase === "idle" && event.text.includes("ESSAY")) {
			phase = "aborting";
			setTimeout(() => {
				log("aborting now");
				ctx.abort();
			}, 6000);
		}
		return { action: "continue" };
	});
}
