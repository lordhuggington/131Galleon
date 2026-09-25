import type { ReactNode } from "react";

type PillVariant = "default" | "ok" | "warn" | "oat";

const VARIANT_CLASS: Record<PillVariant, string> = { default: "", ok: "ok", warn: "warn", oat: "oat" };

export function Pill({
  variant = "default",
  className = "",
  children,
}: {
  variant?: PillVariant;
  className?: string;
  children: ReactNode;
}) {
  return <span className={["pill", VARIANT_CLASS[variant], className].filter(Boolean).join(" ")}>{children}</span>;
}
