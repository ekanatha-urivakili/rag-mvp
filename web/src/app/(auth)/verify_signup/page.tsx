import { VerifySignupForm } from "@/components/auth-forms";
import { linkToken } from "@/lib/link-token";

export default async function Page({ searchParams }: PageProps<"/verify_signup">) {
  return <VerifySignupForm token={await linkToken(searchParams)} />;
}
