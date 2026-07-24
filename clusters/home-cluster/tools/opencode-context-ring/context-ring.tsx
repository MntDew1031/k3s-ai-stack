/** @jsxImportSource @opentui/solid */
/**
 * A local OpenCode TUI companion to the Open WebUI context meter.
 *
 * Terminals cannot draw the browser SVG used by Open WebUI, so this renders a
 * compact, colour-coded Unicode circular meter beside the composer and in the
 * session sidebar. It makes no network calls and does not inspect chat text.
 */
import { createMemo } from "solid-js";
import type { TuiPluginModule } from "@opencode-ai/plugin/tui";

const LOCAL_CONTEXT_DEFAULT = 32_768;

type Meter = {
  tokens: number;
  limit: number;
  percent: number;
  label: string;
  color: "accent" | "warning" | "error" | "textMuted";
};

function formatTokens(value: number): string {
  return Math.max(0, Math.round(value)).toLocaleString("en-US");
}

function ringGlyph(percent: number): string {
  if (percent >= 99) return "●";
  if (percent >= 88) return "◕";
  if (percent >= 63) return "◑";
  if (percent >= 38) return "◐";
  if (percent >= 13) return "◔";
  return "○";
}

function meterFor(api: Parameters<TuiPluginModule["tui"]>[0], sessionID: string): Meter {
  const messages = api.state.session.messages(sessionID);
  const assistant = [...messages]
    .reverse()
    .find((message) => message.role === "assistant" && Boolean(message.tokens));

  if (!assistant || assistant.role !== "assistant") {
    return {
      tokens: 0,
      limit: LOCAL_CONTEXT_DEFAULT,
      percent: 0,
      label: "Awaiting first response",
      color: "textMuted",
    };
  }

  const model = api.state.provider
    .find((provider) => provider.id === assistant.providerID)
    ?.models[assistant.modelID];
  const limit = model?.limit?.context ?? LOCAL_CONTEXT_DEFAULT;
  // OpenCode records the exact prompt tokens used to generate the latest
  // answer, including its system and tool context.
  const tokens =
    assistant.tokens.input +
    assistant.tokens.output +
    assistant.tokens.reasoning +
    assistant.tokens.cache.read +
    assistant.tokens.cache.write;
  const percent = Math.min(100, Math.round((tokens / Math.max(1, limit)) * 100));

  return {
    tokens,
    limit,
    percent,
    label: model?.name ?? assistant.modelID,
    color: percent >= 90 ? "error" : percent >= 70 ? "warning" : "accent",
  };
}

function ContextRing(props: {
  api: Parameters<TuiPluginModule["tui"]>[0];
  sessionID: string;
  compact?: boolean;
}) {
  // OpenCode's state store is reactive. Reading it in this memo updates the
  // meter automatically after each response without polling or a restart.
  const meter = createMemo(() => meterFor(props.api, props.sessionID));
  const theme = () => props.api.theme.current;
  const meterColor = () => theme()[meter().color];

  if (props.compact) {
    return (
      <box flexDirection="row" gap={1} alignItems="center" paddingLeft={1} paddingRight={1}>
        <text fg={meterColor()}>{ringGlyph(meter().percent)}</text>
        <text fg={meterColor()}>{`${meter().percent}%`}</text>
      </box>
    );
  }

  return (
    <box width="100%" flexDirection="column" marginBottom={1} paddingTop={1} paddingBottom={1}>
      <box flexDirection="row" gap={1}>
        <text fg={meterColor()}>{ringGlyph(meter().percent)}</text>
        <text fg={theme().text}>Context window</text>
        <text fg={meterColor()}>{`${meter().percent}%`}</text>
      </box>
      <text fg={theme().textMuted}>{`${formatTokens(meter().tokens)} / ${formatTokens(meter().limit)} tokens`}</text>
      <text fg={theme().textMuted}>{meter().label}</text>
    </box>
  );
}

const ContextRingPlugin: TuiPluginModule & { id: string } = {
  id: "local-context-ring",
  tui: async (api) => {
    api.slots.register({
      order: 90,
      slots: {
        session_prompt_right(_ctx, props) {
          return <ContextRing api={api} sessionID={props.session_id} compact />;
        },
        sidebar_content(_ctx, props) {
          return <ContextRing api={api} sessionID={props.session_id} />;
        },
      },
    });
  },
};

export default ContextRingPlugin;
