import type { Metadata } from "next";
import { ConversationsPage } from "../../components/conversations/ConversationsPage";

export const metadata: Metadata = {
  title: "Conversations",
  robots: { index: false, follow: false },
};

export default function ConversationsRoute() {
  return <ConversationsPage />;
}
