export function navigationFor(permissions: readonly string[], pathname: string) {
  return [
    { href: "/", label: "Chat", allowed: permissions.includes("chat:use") },
    { href: "/documents", label: "Documents", allowed: permissions.includes("document:read") },
    { href: "/receipts", label: "Receipts", allowed: permissions.includes("document:read") },
    { href: "/members", label: "Members", allowed: permissions.includes("member:manage") },
    {
      href: "/settings",
      label: "Settings",
      allowed: permissions.includes("apikey:manage") || permissions.includes("audit:read"),
    },
  ]
    .filter((item) => item.allowed)
    .map((item) => ({
      ...item,
      active: item.href === "/" ? pathname === "/" || pathname.startsWith("/c/") : pathname === item.href,
    }));
}
