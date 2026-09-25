// Display-only phone formatting. The server owns normalisation (spec §6.3).

export const digitsOnly = (s: string): string => s.replace(/\D+/g, "");

/** "+13105551234" -> "(310) 555-1234". Anything not a US number comes back unchanged. */
export function prettyPhone(phone: string | null | undefined): string {
  if (!phone) return "";
  const d = digitsOnly(phone);
  const local = d.length === 11 && d.startsWith("1") ? d.slice(1) : d;
  if (local.length !== 10) return phone;
  return `(${local.slice(0, 3)}) ${local.slice(3, 6)}-${local.slice(6)}`;
}
