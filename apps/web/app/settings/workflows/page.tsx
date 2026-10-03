import { redirect } from "next/navigation";

// Legacy address: Workflows moved to /workflows (one implementation, no loop).
export default function LegacyWorkflowsPage() {
  redirect("/workflows");
}
