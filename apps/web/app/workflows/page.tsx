import type { Metadata } from "next";
import { WorkflowsSettings } from "../../components/workflows/WorkflowsSettings";

export const metadata: Metadata = {
  title: "Workflows",
  robots: { index: false, follow: false },
};

export default function WorkflowsPage() {
  return <WorkflowsSettings />;
}
