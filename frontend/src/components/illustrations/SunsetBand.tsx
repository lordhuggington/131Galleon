import { useId, type ReactNode } from "react";

export function SunsetBand({ children }: { children?: ReactNode }) {
  // useId() contains punctuation; strip it so the url(#…) reference is a plain id.
  const gradientId = `sunset-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  return (
    <div className="band">
      <svg viewBox="0 0 320 190" preserveAspectRatio="xMidYMid slice" width="100%" aria-hidden="true" focusable="false">
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#FFCE7B" />
            <stop offset=".55" stopColor="#FF9E64" />
            <stop offset="1" stopColor="#F2708A" />
          </linearGradient>
        </defs>
        {/* sky */}
        <rect width="320" height="190" fill={`url(#${gradientId})`} />
        {/* sun */}
        <circle cx="160" cy="108" r="40" fill="#FFE8B0" />
        {/* water: two wave layers */}
        <path d="M0 128 Q80 116 160 128 T320 128 V190 H0 Z" fill="#1C7C8C" />
        <path d="M0 142 Q80 132 160 142 T320 142 V190 H0 Z" fill="#155E6B" />
        {/* sand */}
        <path d="M0 166 Q160 156 320 166 V190 H0 Z" fill="#E9C89B" />
        {/* surfboard, planted in the sand, right */}
        <g transform="translate(226,120) rotate(14)">
          <ellipse cx="0" cy="0" rx="9" ry="32" fill="#F4E1C6" stroke="#8C4A2F" strokeWidth="2.5" />
          <line x1="0" y1="-28" x2="0" y2="28" stroke="#E76F2C" strokeWidth="3" />
        </g>
        {/* camper van, left */}
        <g transform="translate(30,128)">
          <rect x="0" y="6" width="58" height="26" rx="8" fill="#FDF1DC" stroke="#5A3E36" strokeWidth="2.5" />
          <path d="M0 18 h58" stroke="#E76F2C" strokeWidth="6" />
          <rect x="8" y="10" width="12" height="8" rx="2" fill="#9AD4D6" />
          <rect x="24" y="10" width="12" height="8" rx="2" fill="#9AD4D6" />
          <circle cx="14" cy="34" r="6" fill="#5A3E36" />
          <circle cx="46" cy="34" r="6" fill="#5A3E36" />
        </g>
        {/* gulls */}
        <path
          d="M96 52 q6 -6 12 0 M120 64 q6 -6 12 0"
          fill="none"
          stroke="#FFF3E0"
          strokeWidth="2.5"
          strokeLinecap="round"
        />
      </svg>
      {children ? <div className="band-text">{children}</div> : null}
    </div>
  );
}
