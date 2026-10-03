import type { Metadata } from "next";
import { KnowledgeSettings } from "../../../components/knowledge/KnowledgeSettings";

export const metadata: Metadata = {
  title: "Knowledge",
  robots: { index: false, follow: false },
};

export default function KnowledgePage() {
  return <KnowledgeSettings />;
}
