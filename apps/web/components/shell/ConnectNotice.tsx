// Shown by an authenticated page while no key is connected. The key itself is entered only
// in the shell's Session control (or the Overview's Connect control), never per page.
import Link from "next/link";

export function ConnectNotice({ area }: { area: string }) {
  return (
    <div className="notice notice--neutral" role="status">
      Connect to the Product API to view {area}. Use <strong>Session</strong> in the navigation panel, or{" "}
      <Link href="/">connect from the Overview</Link>.
    </div>
  );
}
