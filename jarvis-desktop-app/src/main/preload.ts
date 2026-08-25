/**
 * The four things the console page may ask of the shell.
 *
 * Everything else — the filesystem, the network, node — is not exposed, and
 * `contextIsolation` is what makes that a boundary rather than a convention.
 * The console is a web page; this is the whole of its extra API.
 */

import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("jarvisDesktop", {
  /** True inside the shell, undefined in a browser. The console feature-detects. */
  present: true,

  /** Called when the push-to-talk key is pressed anywhere on the machine. */
  onPushToTalk: (fn: () => void) => {
    ipcRenderer.on("jarvis:push-to-talk", () => fn());
  },

  /** Called when the tray's mute item is toggled. */
  onMute: (fn: (muted: boolean) => void) => {
    ipcRenderer.on("jarvis:mute", (_event, muted: boolean) => fn(Boolean(muted)));
  },

  /** Called with the agent's state, and again on every change. */
  onStatus: (fn: (status: { state: string; detail?: string }) => void) => {
    ipcRenderer.on("jarvis:status", (_event, status) => fn(status));
  },

  /** Called when the desktop agent needs a human to approve an action. */
  onAsk: (fn: (request: Record<string, unknown>) => void) => {
    ipcRenderer.on("jarvis:ask", (_event, request) => fn(request));
  },

  /** Answer one held action. */
  answer: (id: string, verdict: "approved" | "approved_always" | "denied") =>
    ipcRenderer.invoke("jarvis:answer", id, verdict),

  /** What the shell knows right now, for a page that loaded late. */
  state: () => ipcRenderer.invoke("jarvis:state"),
});
