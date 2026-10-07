import { AcceptInviteForm } from "@/components/auth-forms";
import { linkToken } from "@/lib/link-token";

export default async function Page({ searchParams }: PageProps<"/accept_invite">) {
  return <AcceptInviteForm token={await linkToken(searchParams)} />;
}
