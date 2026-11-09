/**
 * Test-only extension: logs the exact serialized provider payload of every request.
 * Used to prove a retried request carries the same content as the interrupted one.
 */
import { appendFileSync, writeFileSync } from "node:fs";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const LOG = process.env.PI_RETRY_PAYLOAD_LOG ?? "/tmp/pi-retry-payload.jsonl";

writeFileSync(LOG, "");

export default function (pi: ExtensionAPI) {
	let request = 0;
	pi.on("before_provider_request", (event) => {
		request += 1;
		appendFileSync(LOG, `${JSON.stringify({ request, payload: event.payload })}\n`);
	});
}
