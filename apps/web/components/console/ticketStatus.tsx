// Neutral labels for the Product's ticket command statuses. Only "verified" is shown as
// a created ticket; the backend decides the status, this only names it.
import { Badge, type Tone } from "./ui";

const STATUS: Record<string, [string, Tone, string]> = {
  verified: ["Verified", "success", "The ticket was created and verified."],
  in_progress: ["In progress", "pending", "Not confirmed yet. Refresh the command status later."],
  awaiting_approval: ["Awaiting approval", "attention", "Not created. The command needs an approval; no approval action exists in this console."],
  requires_human: ["Requires human review", "attention", "Not confirmed. The outcome needs a person to review it."],
  denied: ["Denied", "danger", "Not created. The command was denied."],
  failed: ["Failed", "danger", "Not created. The command failed."],
};

export function TicketStatus({ status }: { status: string }) {
  const [text, tone] = STATUS[status] ?? [status, "neutral" as Tone];
  return <Badge tone={tone}>{text}</Badge>;
}

export function ticketStatusExplanation(status: string): string {
  return STATUS[status]?.[2] ?? "Unrecognized status; see the command status.";
}
