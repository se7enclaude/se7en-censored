import { token } from "./client";
import type { AppEvent } from "./types";

type Handler = (ev: AppEvent) => void;
type StatusHandler = (connected: boolean) => void;

/** Auto-reconnecting event stream from the backend. */
export class EventStream {
  private ws: WebSocket | null = null;
  private handlers = new Set<Handler>();
  private statusHandlers = new Set<StatusHandler>();
  private retry = 0;
  private closed = false;
  private timer: number | undefined;

  connect(): void {
    this.closed = false;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/api/ws?token=${encodeURIComponent(token)}`);
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 0;
      this.statusHandlers.forEach((h) => h(true));
    };
    ws.onmessage = (m) => {
      try {
        const ev = JSON.parse(m.data) as AppEvent;
        this.handlers.forEach((h) => h(ev));
      } catch {
        /* ignore malformed frames */
      }
    };
    ws.onclose = () => {
      this.statusHandlers.forEach((h) => h(false));
      if (this.closed) return;
      const delay = Math.min(8000, 400 * 2 ** this.retry++);
      this.timer = window.setTimeout(() => this.connect(), delay);
    };
    ws.onerror = () => ws.close();
  }

  close(): void {
    this.closed = true;
    window.clearTimeout(this.timer);
    this.ws?.close();
  }

  on(h: Handler): () => void {
    this.handlers.add(h);
    return () => this.handlers.delete(h);
  }

  onStatus(h: StatusHandler): () => void {
    this.statusHandlers.add(h);
    return () => this.statusHandlers.delete(h);
  }
}

export const events = new EventStream();
