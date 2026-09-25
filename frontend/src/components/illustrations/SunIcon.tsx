export function SunIcon({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true" focusable="false">
      <rect width="64" height="64" rx="14" fill="#FFF3E0" />
      <circle cx="32" cy="30" r="14" fill="#E76F2C" />
      <path d="M0 40 Q16 34 32 40 T64 40 V64 H0 Z" fill="#1C7C8C" />
      <path d="M0 48 Q16 42 32 48 T64 48 V64 H0 Z" fill="#155E6B" />
    </svg>
  );
}
