export function navigationFor(permissions: readonly string[], pathname: string) {
  return [
    { href: "/", label: "Home", allowed: permissions.includes("chat:use") },
    { href: "/documents", label: "Upload docs", allowed: permissions.includes("document:read") },
    { href: "/receipts", label: "Scan receipts", allowed: permissions.includes("document:read") },
    { href: "/members", label: "Members", allowed: permissions.includes("member:manage") },
    {
      href: "/settings",
      label: "Settings",
      allowed: true,
    },
  ]
    .filter((item) => item.allowed)
    .map((item) => ({
      ...item,
      active: item.href === "/" ? pathname === "/" || pathname.startsWith("/c/") : pathname === item.href,
    }));
}
