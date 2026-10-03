import type { Metadata } from "next";
import { Console } from "../../components/console/Console";
import { tabFrom } from "../../components/console/tabs";

export const metadata: Metadata = {
  title: "Operations",
  robots: { index: false, follow: false },
};

// ?tab=analysis|report|ticket|command selects a section; anything else falls back to
// analysis. Selecting a tab never runs anything.
export default async function OperationsPage({ searchParams }: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { tab } = await searchParams;
  return <Console initialTab={tabFrom(tab)} />;
}
