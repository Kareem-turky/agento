"use client";

// The one Agento application shell: branding, Product navigation, the session control,
// API reachability and the responsive layout. Pages render only their own content (with
// one PageHeader <h1>); they never render a header, a navigation or a key form.
// Task 042: the global "Ask Agento" control opens the Employee Chat drawer from any page;
// opening it never calls the model.
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { ChatDrawer } from "../chat/ChatDrawer";
import { ProductNavigation } from "./ProductNavigation";
import { HealthBadge, SessionControl } from "./SessionControl";

export function AppShell({ children }: { children: ReactNode }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const closeChat = useCallback(() => setChatOpen(false), []);
  const pathname = usePathname();

  // A navigation closes the mobile menu.
  useEffect(() => {
    setMenuOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  return (
    <div className="app">
      <a className="skip-link" href="#main-content">Skip to content</a>

      <header className="app__mobilebar">
        <Brand />
        <div className="app__mobilebar-actions">
          <HealthBadge />
          <AskAgentoButton open={chatOpen} onOpen={() => setChatOpen(true)} />
          <button
            type="button"
            className="button button--ghost button--small app__menu-button"
            aria-expanded={menuOpen}
            aria-controls="product-sidebar"
            onClick={() => setMenuOpen((open) => !open)}
          >
            {menuOpen ? "Close menu" : "Menu"}
          </button>
        </div>
      </header>

      <aside id="product-sidebar" className={`app__sidebar${menuOpen ? " app__sidebar--open" : ""}`}>
        <div className="app__sidebar-brand"><Brand /></div>
        <AskAgentoButton open={chatOpen} onOpen={() => setChatOpen(true)} />
        <ProductNavigation onNavigate={() => setMenuOpen(false)} />
        <SessionControl />
      </aside>

      <main id="main-content" className="app__main" tabIndex={-1}>
        {children}
      </main>

      <ChatDrawer open={chatOpen} onClose={closeChat} />
    </div>
  );
}

function AskAgentoButton({ open, onOpen }: { open: boolean; onOpen: () => void }) {
  return (
    <button
      type="button"
      className="button button--small ask-agento"
      aria-haspopup="dialog"
      aria-expanded={open}
      onClick={onOpen}
    >
      Ask Agento
    </button>
  );
}

function Brand() {
  return (
    <Link href="/" className="brand" aria-label="Agento, AI Operating Layer: Overview">
      <span className="brand__mark" aria-hidden="true">◆</span>
      <span className="brand__text">
        <span className="brand__name">Agento</span>
        <span className="brand__subtitle">AI Operating Layer</span>
      </span>
    </Link>
  );
}
