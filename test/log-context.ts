/**
 * Test-only extension: logs what each provider request would send, plus the raw session
 * projection that compaction and branch summarization serialize.
 *
 * Load AFTER pi-retry so `context` sees the already-filtered messages.
 */
import { appendFileSync, writeFileSync } from "node:fs";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import type { AgentMessage } from "@earendil-works/pi-agent-core";

const LOG = process.env.PI_RETRY_LOG ?? "/tmp/pi-retry-context.jsonl";
const PROJECTION_LOG = process.env.PI_RETRY_PROJECTION_LOG ?? "/tmp/pi-retry-projection.jsonl";

writeFileSync(LOG, "");
writeFileSync(PROJECTION_LOG, "");

function describe(message: AgentMessage): Record<string, unknown> {
	const text = Array.isArray(message.content)
		? message.content
				.map((block) =>
					block.type === "text"
						? block.text
						: block.type === "thinking"
							? `[thinking] ${block.thinking}`
							: `[${block.type}]`,
				)
				.join("")
				.slice(0, 120)
		: String(message.content ?? "");
	return {
		role: message.role,
		customType: (message as { customType?: string }).customType,
		stopReason: (message as { stopReason?: string }).stopReason,
		text,
	};
}

export default function (pi: ExtensionAPI) {
	let request = 0;

	pi.on("context", (event) => {
		request += 1;
		appendFileSync(LOG, `${JSON.stringify({ request, messages: event.messages.map(describe) })}\n`);
	});

	// Compaction and branch summarization serialize this projection instead of running
	// `context` hooks, so a hidden message that survives here would reach a provider.
	pi.on("agent_settled", (_event, ctx) => {
		const messages = ctx.sessionManager.buildSessionProjection().messages;
		appendFileSync(PROJECTION_LOG, `${JSON.stringify({ requestsSoFar: request, messages: messages.map(describe) })}\n`);
	});
}
