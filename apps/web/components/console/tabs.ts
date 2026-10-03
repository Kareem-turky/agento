// The Operations sections and their deep-link parsing. Shared by the server page (which
// reads ?tab=) and the client console; a tab only selects a section and never runs anything.
export const TABS = [
  { id: "analysis", label: "Analyze operations" },
  { id: "report", label: "Daily report" },
  { id: "ticket", label: "Operational ticket" },
  { id: "command", label: "Command status" },
] as const;

export type TabId = (typeof TABS)[number]["id"];

/** A deep-link tab; anything unknown falls back to the analysis tab. */
export function tabFrom(value: string | string[] | undefined): TabId {
  const candidate = Array.isArray(value) ? value[0] : value;
  return TABS.find((item) => item.id === candidate)?.id ?? "analysis";
}
