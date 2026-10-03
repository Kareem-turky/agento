"use client";

// The one Product navigation: Work and Configure. Internal framework or runtime names are
// never navigation labels.
import Link from "next/link";
import { usePathname } from "next/navigation";

export const NAVIGATION = [
  {
    group: "Work",
    items: [
      { href: "/", label: "Overview" },
      { href: "/operations", label: "Operations" },
      { href: "/approvals", label: "Approvals" },
      { href: "/conversations", label: "Conversations" },
      { href: "/workflows", label: "Workflows" },
    ],
  },
  {
    group: "Configure",
    items: [
      { href: "/settings/agents", label: "Agents" },
      { href: "/settings/integrations", label: "Integrations" },
      { href: "/settings/knowledge", label: "Knowledge" },
      { href: "/system", label: "System" },
    ],
  },
] as const;

function isActive(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}

export function ProductNavigation({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname() ?? "/";
  return (
    <nav className="product-nav" aria-label="Product">
      {NAVIGATION.map((section) => (
        <div className="product-nav__group" key={section.group}>
          <h2 className="product-nav__heading" id={`nav-${section.group.toLowerCase()}`}>{section.group}</h2>
          <ul className="product-nav__list" aria-labelledby={`nav-${section.group.toLowerCase()}`}>
            {section.items.map((item) => {
              const active = isActive(pathname, item.href);
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    className="product-nav__link"
                    aria-current={active ? "page" : undefined}
                    onClick={onNavigate}
                  >
                    {item.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}
