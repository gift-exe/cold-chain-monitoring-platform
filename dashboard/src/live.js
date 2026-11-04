// Live updates: the API's WebSocket says which kinds of data changed (incident, notification,
// consignment, custody_handoff, device_status); views that show those kinds fetch again. If the
// connection drops, it is reopened, and views also poll as a fallback.
import { token } from "./auth.js";

const listeners = new Set();
let connected = false;
const statusListeners = new Set();

function setConnected(v) {
  connected = v;
  statusListeners.forEach((f) => f(v));
}

export function onConnection(f) {
  statusListeners.add(f);
  f(connected);
  return () => statusListeners.delete(f);
}

export function onChange(kinds, f) {
  const entry = { kinds: new Set(kinds), f };
  listeners.add(entry);
  return () => listeners.delete(entry);
}

export function startLive() {
  let retry = 1000;
  const open = async () => {
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/ws`);
    ws.onopen = async () => {
      ws.send(await token());
      setConnected(true);
      retry = 1000;
    };
    ws.onmessage = (e) => {
      const changed = JSON.parse(e.data).changed || [];
      listeners.forEach(({ kinds, f }) => {
        if (changed.some((k) => kinds.has(k))) f();
      });
    };
    ws.onclose = () => {
      setConnected(false);
      setTimeout(open, retry);
      retry = Math.min(retry * 2, 30000);
    };
  };
  open();
}
