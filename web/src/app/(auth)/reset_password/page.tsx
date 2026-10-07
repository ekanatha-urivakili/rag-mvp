import { ResetPasswordForm } from "@/components/auth-forms";
import { linkToken } from "@/lib/link-token";

export default async function Page({ searchParams }: PageProps<"/reset_password">) {
  return <ResetPasswordForm token={await linkToken(searchParams)} />;
}
