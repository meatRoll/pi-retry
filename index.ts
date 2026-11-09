/**
 * pi-retry — send the last request again without typing anything.
 *
 * `/retry` starts a new agent run from the current transcript. The hidden message that
 * starts the run never reaches the model: it is stripped from every provider request and
 * omitted from the session projection once the run produced its first turn.
 *
 * What the model sees afterwards:
 *   - interrupted turn (aborted/error): the request is byte-identical to the request that
 *     was interrupted. Pi already drops those turns in pi-ai's transform-messages.
 *   - completed answer: the trailing answer is left out of the retried request, so the
 *     model answers the same prompt again.
 *
 * Only type imports are used at runtime; Pi supplies the host packages.
 */

import type {
	ExtensionAPI,
	ExtensionCommandContext,
	ExtensionContext,
	SessionBoundaryDraft,
} from "@earendil-works/pi-coding-agent";
import type { AssistantMessage } from "@earendil-works/pi-ai";
import type { AgentMessage } from "@earendil-works/pi-agent-core";

/** customType of the hidden message that starts a retry run. */
const TRIGGER_TYPE = "pi-retry/trigger";
/** Text of the hidden trigger. Only visible if a code path bypasses the context filter. */
const TRIGGER_TEXT = "continue";

/** Whether the current run is a retry run (armed by the command, cleared when it settles). */
let armed = false;

/**
 * Assistant turns removed from the transcript for the current retry run. Captured on the
 * first request and re-applied to every later request of the same run, because after the
 * first tool call the dropped turn would otherwise sit in the middle of the history again
 * and the model would see the old answer next to the new one.
 */
let droppedKeys: Set<string> | undefined;

function isTrigger(message: AgentMessage): boolean {
	return message.role === "custom" && (message as { customType?: string }).customType === TRIGGER_TYPE;
}

function isAssistant(message: AgentMessage | undefined): message is AssistantMessage {
	return message?.role === "assistant";
}

function hasToolCalls(message: AssistantMessage): boolean {
	return message.content.some((block) => block.type === "toolCall");
}

function assistantKey(message: AgentMessage): string | undefined {
	return isAssistant(message) ? `assistant:${message.timestamp}` : undefined;
}

/**
 * Strip the hidden trigger and, during a retry run, the trailing assistant turn.
 *
 * A trailing turn is only dropped when it cannot own tool results; dropping an assistant
 * message that has results after it would leave orphan tool results, which providers reject.
 */
function rewriteContext(messages: AgentMessage[], retrying: boolean): AgentMessage[] {
	let result = messages;
	let changed = false;

	if (result.some(isTrigger)) {
		result = result.filter((message) => !isTrigger(message));
		changed = true;
	}

	if (retrying) {
		if (!droppedKeys) {
			const last = result[result.length - 1];
			if (isAssistant(last) && !hasToolCalls(last)) {
				droppedKeys = new Set([assistantKey(last) as string]);
			}
		}
		if (droppedKeys) {
			const keys = droppedKeys;
			const kept = result.filter((message) => {
				const key = assistantKey(message);
				return key === undefined || !keys.has(key);
			});
			if (kept.length !== result.length) {
				result = kept;
				changed = true;
			}
		}
	}

	return changed ? result : messages;
}

function stopReasonOf(messages: AgentMessage[]): string | undefined {
	const last = messages[messages.length - 1];
	return isAssistant(last) ? last.stopReason : undefined;
}

/**
 * Trigger entries that should be omitted from the projection once the retry run produced its
 * first turn. Compaction and branch summarization serialize the projection instead of running
 * `context` hooks, so a trigger left there would reach a provider in a summary prompt.
 */
function triggerOmissions(ctx: ExtensionContext): SessionBoundaryDraft[] {
	const branch = ctx.sessionManager.getBranch();
	const omitted = new Set(
		branch
			.filter((entry) => entry.type === "context_edit")
			.map((entry) => (entry as { targetId: string }).targetId),
	);

	return branch
		.filter(
			(entry) =>
				entry.type === "custom_message" && entry.customType === TRIGGER_TYPE && !omitted.has(entry.id),
		)
		.map((entry) => ({ type: "context_edit", targetId: entry.id, replacement: null }) satisfies SessionBoundaryDraft);
}

/** Everything `retry` needs; command and shortcut contexts both satisfy it. */
type RetryContext = Pick<ExtensionContext, "ui" | "isIdle" | "sessionManager">;

export async function retry(pi: ExtensionAPI, ctx: RetryContext, args: string): Promise<void> {
	const words = args.trim().toLowerCase().split(/\s+/).filter(Boolean);

	if (words.some((word) => word === "help" || word === "--help" || word === "-h")) {
		ctx.ui.notify("pi-retry: /retry sends the last request again (alt+r does the same)", "info");
		return;
	}

	if (!ctx.isIdle()) {
		ctx.ui.notify("pi-retry: the agent is still streaming — press Esc first, then /retry", "warning");
		return;
	}

	const messages = ctx.sessionManager.buildSessionProjection().messages;
	if (messages.length === 0) {
		ctx.ui.notify("pi-retry: nothing to retry yet", "warning");
		return;
	}

	const stopReason = stopReasonOf(messages);

	armed = true;
	ctx.ui.setStatus("pi-retry", "↻ retrying…");
	try {
		pi.sendMessage(
			{
				customType: TRIGGER_TYPE,
				content: [{ type: "text", text: TRIGGER_TEXT }],
				display: false,
			},
			{ triggerTurn: true },
		);
	} catch (error) {
		armed = false;
		droppedKeys = undefined;
		ctx.ui.setStatus("pi-retry", undefined);
		throw error;
	}

	ctx.ui.notify(
		`pi-retry: re-sending the request${stopReason ? ` (last stop: ${stopReason})` : ""}`,
		"info",
	);
}

export default function piRetry(pi: ExtensionAPI) {
	// The trigger exists only to start the run; it never reaches the model.
	const strip = (event: { messages: AgentMessage[] }) => {
		const next = rewriteContext(event.messages, armed);
		return next === event.messages ? undefined : { messages: next };
	};
	pi.on("context", strip);
	pi.on("context_with_system", strip);

	// The first completed turn is the earliest point where the trigger can be omitted durably.
	pi.on("turn_end", (event, ctx) => {
		if (!armed) return;
		const drafts = triggerOmissions(ctx);
		if (drafts.length === 0) return;
		return { entries: [...event.entries, ...drafts] };
	});

	const reset = () => {
		armed = false;
		droppedKeys = undefined;
	};
	pi.on("session_start", reset);
	pi.on("session_shutdown", reset);
	pi.on("agent_settled", (_event, ctx) => {
		reset();
		ctx.ui.setStatus("pi-retry", undefined);
	});

	pi.registerCommand("retry", {
		description: "Send the last request again (interrupted turn, or the last answer)",
		handler: async (args, ctx: ExtensionCommandContext) => {
			await retry(pi, ctx, args);
		},
	});

	// One keystroke for the common case.
	pi.registerShortcut("alt+r", {
		description: "Retry the last request",
		handler: async (ctx) => {
			await retry(pi, ctx, "");
		},
	});
}

export { TRIGGER_TYPE };
