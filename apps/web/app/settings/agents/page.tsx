import type { Metadata } from "next";
import { AgentsSettings } from "../../../components/agents/AgentsSettings";

export const metadata: Metadata = {
  title: "Agents",
  robots: { index: false, follow: false },
};

export default function AgentsPage() {
  return <AgentsSettings />;
}
