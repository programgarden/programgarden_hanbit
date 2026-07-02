"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/** The 6 fixed left-nav routes (.claude/plans/2026-06-20-통합계획서.md §6 (Part I) / wireframes). */
const NAV = [
  { href: "/", label: "Overview" },
  { href: "/positions", label: "Positions" },
  { href: "/orders", label: "Orders" },
  { href: "/charts", label: "Charts" },
  { href: "/strategies", label: "Strategies" },
  { href: "/risk", label: "Risk" },
] as const;

/**
 * Left navigation (fixed across all screens). Highlights the current route
 * with a leading ● marker, matching the wireframes.
 */
export function SideNav() {
  const pathname = usePathname();

  return (
    <nav className="flex w-44 shrink-0 flex-col gap-0.5 border-r border-border bg-surface p-2">
      {NAV.map((item) => {
        const active =
          item.href === "/"
            ? pathname === "/"
            : pathname.startsWith(item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            className={`flex items-center gap-2 rounded px-3 py-2 text-sm transition-colors ${
              active
                ? "bg-surface-2 font-semibold text-foreground"
                : "text-muted hover:bg-surface-2 hover:text-foreground"
            }`}
          >
            <span className={active ? "text-accent" : "text-transparent"}>
              ●
            </span>
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
