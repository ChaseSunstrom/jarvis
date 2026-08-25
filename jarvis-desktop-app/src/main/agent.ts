/**
 * The shell's side of the desktop agent's loopback socket
 * (`jarvis-desktop/jarvis_desktop/ipc.py`).
 *
 * One line of JSON per frame. The agent sends `status` and `ask`; the shell
 * sends `answer`. Everything about the protocol that matters is on the Python
 * side's docstring, including why it is a TCP port on 127.0.0.1 rather than a
 * Unix socket.
 *
 * This class is deliberately dumb: it reconnects, it parses, and it hands
 * frames to callbacks. What a consent prompt LOOKS like is the renderer's job,
 * and whether an action may run at all was decided by the agent long before
 * either of them was involved.
 */

import { Socket } from "node:net";
import { EventEmitter } from "node:events";

export interface AskFrame {
  id: string;
  action_id: string;
  description: string;
  params: Record<string, unknown>;
  tier: number;
  reason: string;
  allow_always?: boolean;
}

export interface StatusFrame {
  state: string;
  detail?: string;
}

/** How long to wait before dialling again. */
const RETRY_MS = 2000;

export class AgentLink extends EventEmitter {
  private socket: Socket | null = null;
  private buffer = "";
  private closed = false;
  private timer: NodeJS.Timeout | null = null;

  constructor(
    private readonly port: number,
    private readonly token: string,
    private readonly retryMs: number = RETRY_MS,
  ) {
    super();
  }

  get connected(): boolean {
    return this.socket !== null && !this.socket.destroyed;
  }

  connect(): void {
    if (this.closed || !this.port || !this.token) return;
    const socket = new Socket();
    this.socket = socket;
    socket.setEncoding("utf8");
    socket.on("data", (chunk: string) => this.absorb(chunk));
    socket.on("error", () => this.retry());
    socket.on("close", () => this.retry());
    socket.connect(this.port, "127.0.0.1", () => {
      // The token first, on its own line: the agent counts the connection only
      // once it has seen it, so an unauthenticated shell is never "connected"
      // and a question is reported as unattended rather than waiting on it.
      socket.write(JSON.stringify({ token: this.token }) + "\n");
      this.emit("open");
    });
  }

  /** Answer one held action. Single use on the agent's side. */
  answer(id: string, verdict: "approved" | "approved_always" | "denied"): void {
    if (!this.connected) return;
    this.socket?.write(JSON.stringify({ type: "answer", id, verdict }) + "\n");
  }

  close(): void {
    this.closed = true;
    if (this.timer) clearTimeout(this.timer);
    this.socket?.destroy();
    this.socket = null;
  }

  private absorb(chunk: string): void {
    this.buffer += chunk;
    for (;;) {
      const newline = this.buffer.indexOf("\n");
      if (newline === -1) break;
      const line = this.buffer.slice(0, newline).trim();
      this.buffer = this.buffer.slice(newline + 1);
      if (!line) continue;
      let frame: Record<string, unknown>;
      try {
        frame = JSON.parse(line);
      } catch {
        continue; // a half-written line, or something that is not the agent
      }
      if (frame.type === "status") this.emit("status", frame as unknown as StatusFrame);
      else if (frame.type === "ask") this.emit("ask", frame as unknown as AskFrame);
    }
  }

  private retry(): void {
    this.socket?.destroy();
    this.socket = null;
    this.emit("closed");
    if (this.closed) return;
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => this.connect(), this.retryMs);
  }
}

/** Split a stream of newline-delimited JSON, for the unit tests. */
export function parseFrames(text: string): Record<string, unknown>[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      try {
        return JSON.parse(line) as Record<string, unknown>;
      } catch {
        return null;
      }
    })
    .filter((frame): frame is Record<string, unknown> => frame !== null);
}
