/**
 * What the tray menu says, as data.
 *
 * Separate from the Electron call that draws it so it can be tested in Node:
 * the interesting part of a tray menu is not `new Tray()`, it is whether the
 * labels tell the truth about what the agent is doing.
 */

export type AgentState = "starting" | "idle" | "listening" | "thinking" | "speaking" | "offline";

export interface MenuItemSpec {
  id: string;
  label: string;
  enabled?: boolean;
  checked?: boolean;
  type?: "normal" | "separator" | "checkbox";
}

/** One line describing the agent, for the tray's tooltip and its first row. */
export function statusLabel(state: AgentState, detail = ""): string {
  const base: Record<AgentState, string> = {
    starting: "Starting…",
    idle: "Ready",
    listening: "Listening",
    thinking: "Working",
    speaking: "Speaking",
    // Not "Error": the agent not being there is the ordinary case for anybody
    // who has not started it, and an error icon on a first run is a support
    // ticket.
    offline: "Agent not running",
  };
  const line = base[state] ?? base.idle;
  return detail ? `${line} — ${detail}` : line;
}

export function trayMenu(options: {
  state: AgentState;
  detail?: string;
  muted: boolean;
  pushToTalk: string;
}): MenuItemSpec[] {
  return [
    { id: "status", label: statusLabel(options.state, options.detail), enabled: false },
    { id: "sep-1", label: "", type: "separator" },
    { id: "show", label: "Open Jarvis" },
    {
      id: "push-to-talk",
      // The accelerator is in the label rather than as Electron's `accelerator`
      // field: this one is a GLOBAL shortcut owned by `globalShortcut`, and
      // putting it on the menu item would register it twice and give the
      // second registration a silent failure.
      label: `Push to talk (${options.pushToTalk})`,
    },
    {
      id: "mute",
      label: options.muted ? "Unmute the microphone" : "Mute the microphone",
      type: "checkbox",
      checked: options.muted,
    },
    { id: "sep-2", label: "", type: "separator" },
    { id: "quit", label: "Quit Jarvis" },
  ];
}
