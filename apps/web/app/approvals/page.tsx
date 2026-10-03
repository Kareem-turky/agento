import type { Metadata } from "next";
import { ApprovalsSettings } from "../../components/approvals/ApprovalsSettings";

export const metadata: Metadata = {
  title: "Approvals",
  robots: { index: false, follow: false },
};

export default function ApprovalsPage() {
  return <ApprovalsSettings />;
}
