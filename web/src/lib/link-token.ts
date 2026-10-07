/** Validates the single-use token from an email link (?token=…). Anything malformed is treated as missing. */
export async function linkToken(searchParams: Promise<Record<string, string | string[] | undefined>>) {
  const raw = (await searchParams).token;
  return typeof raw === "string" && /^[A-Za-z0-9_-]{16,256}$/.test(raw) ? raw : null;
}
