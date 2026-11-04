// Status is never shown by colour alone (Section 3.4.7): each has a symbol and a text label too. The
// colours are from the Okabe-Ito palette, which stays distinguishable with common colour-vision
// deficiencies.
export const STATUS = {
  critical: { symbol: "▲", label: "Critical", color: "#D55E00" },
  warning: { symbol: "◆", label: "Warning", color: "#B07800" },
  stale: { symbol: "◌", label: "Stale data", color: "#6B6B6B" },
  ok: { symbol: "●", label: "Normal", color: "#009E73" },
  no_data: { symbol: "○", label: "No data", color: "#8A8A8A" },
};

export function Status({ status, compact = false }) {
  const s = STATUS[status] || STATUS.no_data;
  return (
    <span className={`status status-${status}`} style={{ color: s.color }} title={s.label}>
      <span aria-hidden="true">{s.symbol}</span>
      {compact ? <span className="sr-only">{s.label}</span> : <span className="status-label">{s.label}</span>}
    </span>
  );
}

export function Severity({ severity }) {
  return <Status status={severity === "critical" ? "critical" : "warning"} />;
}

export function Flag({ children, tone = "neutral", title }) {
  return (
    <span className={`flag flag-${tone}`} title={title}>
      {children}
    </span>
  );
}
