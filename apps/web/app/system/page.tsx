import type { Metadata } from "next";
import { SystemPage } from "../../components/system/SystemPage";

export const metadata: Metadata = {
  title: "System",
  robots: { index: false, follow: false },
};

export default function SystemRoute() {
  return <SystemPage />;
}
