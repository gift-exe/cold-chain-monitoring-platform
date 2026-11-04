// The API is served under /api on the dashboard's own origin (Vite proxy in development, nginx in the
// container).
import { useCallback, useEffect, useState } from "react";

import { token } from "./auth.js";
import { onChange } from "./live.js";

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(`/api${path}`, {
    method,
    headers: { Authorization: `Bearer ${await token()}`, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return res.status === 204 ? null : res.json();
}

// Fetch `path`, and fetch it again whenever data of the given kinds changes (see live.js) or every
// `pollMs` as a fallback. Returns { data, error, loading, reload }.
export function useApi(path, kinds = [], pollMs = 60000) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const load = useCallback(() => {
    if (!path) return;
    api(path)
      .then((data) => setState({ data, error: null, loading: false }))
      .catch((error) => setState((s) => ({ ...s, error, loading: false })));
  }, [path]);
  useEffect(() => {
    setState((s) => ({ ...s, loading: true }));
    load();
    const timer = setInterval(load, pollMs);
    const off = onChange(kinds, load);
    return () => {
      clearInterval(timer);
      off();
    };
  }, [load, pollMs, kinds.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps
  return { ...state, reload: load };
}
