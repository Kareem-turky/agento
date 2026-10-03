import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { AppShell } from "../components/shell/AppShell";
import { ProductSessionProvider } from "../components/shell/ProductSessionProvider";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Agento", template: "%s · Agento" },
  description: "AI operating layer for commerce operations.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

// One Product session for the whole application: the provider lives here, above every
// page, so the in-memory key survives client-side navigation (and nothing else).
export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <ProductSessionProvider>
          <AppShell>{children}</AppShell>
        </ProductSessionProvider>
      </body>
    </html>
  );
}
