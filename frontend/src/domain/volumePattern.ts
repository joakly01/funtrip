export function eventAt(event: any, asOf: string) {
  const cutoff = Date.parse(asOf);
  const trace = event.trace.filter((t: any) => Date.parse(t.time) <= cutoff);
  if (!trace.length) return null;
  const last = trace[trace.length - 1];
  return { ...event, trace, state: last.state, label: last.label, assessed_at: last.time,
    reason: last.reason, observation: last.observation, down_streak: last.down_streak,
    bearish_streak: last.bearish_streak, support_test: last.support_test };
}
export function eventsAt(events: any[], asOf: string) {
  return events.map(e => eventAt(e, asOf)).filter(Boolean).reverse();
}
