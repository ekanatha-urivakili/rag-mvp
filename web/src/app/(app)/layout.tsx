import { redirect } from "next/navigation";
import { AppShell } from "@/components/app-shell";
import { getMe } from "@/lib/server-api";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const me = await getMe();
  if (!me) redirect("/login");
  return <AppShell me={me}>{children}</AppShell>;
}
