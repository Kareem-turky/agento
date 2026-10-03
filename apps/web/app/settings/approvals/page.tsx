import { redirect } from "next/navigation";

// Legacy address: Approvals moved to /approvals (one implementation, no loop).
export default function LegacyApprovalsPage() {
  redirect("/approvals");
}
