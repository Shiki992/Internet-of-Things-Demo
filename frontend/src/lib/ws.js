// Build a WebSocket URL relative to the page so the same code works behind
// the Vite dev proxy, the FastAPI static server, and an HTTPS tunnel.
export function wsUrl(path) {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${path}`;
}

// Minimal reconnecting WebSocket: calls onMessage with parsed JSON, retries
// with backoff, and reports status transitions.
export function connect(path, { onMessage, onStatus }) {
  let ws = null;
  let closed = false;
  let attempt = 0;

  const open = () => {
    onStatus?.("connecting");
    ws = new WebSocket(wsUrl(path));
    ws.onopen = () => {
      attempt = 0;
      onStatus?.("open");
    };
    ws.onmessage = (e) => {
      try {
        onMessage(JSON.parse(e.data));
      } catch {
        /* ignore non-JSON frames */
      }
    };
    ws.onclose = () => {
      onStatus?.("closed");
      if (!closed) {
        const delay = Math.min(10000, 500 * 2 ** attempt++);
        setTimeout(open, delay);
      }
    };
    ws.onerror = () => ws.close();
  };
  open();

  return {
    send: (obj) => {
      if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
    },
    close: () => {
      closed = true;
      ws?.close();
    },
  };
}
