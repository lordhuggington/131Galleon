import type { ButtonHTMLAttributes } from "react";

type Variant = "primary" | "secondary" | "danger" | "ghost";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
}

const VARIANT_CLASS: Record<Variant, string> = {
  primary: "primary",
  secondary: "",
  danger: "danger",
  ghost: "ghost",
};

export function Button({ variant = "secondary", className = "", type = "button", ...rest }: ButtonProps) {
  const classes = ["btn", VARIANT_CLASS[variant], className].filter(Boolean).join(" ");
  return <button type={type} className={classes} {...rest} />;
}
