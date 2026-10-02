import type { Metadata } from "next";
import { IntegrationsSettings } from "../../../components/integrations/IntegrationsSettings";

export const metadata: Metadata = {
  title: "Integrations",
  robots: { index: false, follow: false },
};

export default function IntegrationsPage() {
  return <IntegrationsSettings />;
}
