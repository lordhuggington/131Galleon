import type { ReactNode } from "react";

export function Card({ className = "", children }: { className?: string; children: ReactNode }) {
  return <div className={["card", className].filter(Boolean).join(" ")}>{children}</div>;
}
