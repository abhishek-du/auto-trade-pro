/**
 * Prajna brand logo — mark + wordmark.
 *
 * The mark is a rounded-square containing:
 *   • A bold "P" letterform (wisdom)
 *   • A rising candlestick/trend accent line (markets)
 *   • An indigo→violet gradient background
 *
 * Usage:
 *   <PrajnaLogo />                      — full (mark + wordmark)
 *   <PrajnaLogo size={48} text={false}  — mark only, 48 px
 *   <PrajnaLogo size={24} subtitle="AI Trading Intelligence" />
 */
export default function PrajnaLogo({
  size      = 40,
  showText  = true,
  subtitle  = 'Intelligence',
  className = '',
}) {
  const id = 'prajna'; // stable gradient IDs

  return (
    <div className={`flex items-center gap-3 ${className}`}>
      {/* ── Logo mark ── */}
      <svg
        width={size}
        height={size}
        viewBox="0 0 48 48"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        aria-label="Prajna logo"
      >
        <defs>
          {/* Background gradient — deep indigo → violet */}
          <linearGradient id={`${id}-bg`} x1="0" y1="0" x2="48" y2="48" gradientUnits="userSpaceOnUse">
            <stop offset="0%"   stopColor="#312E81" />
            <stop offset="100%" stopColor="#5B21B6" />
          </linearGradient>

          {/* Accent line gradient — violet → cyan */}
          <linearGradient id={`${id}-accent`} x1="0" y1="0" x2="1" y2="0" gradientUnits="objectBoundingBox">
            <stop offset="0%"   stopColor="#A78BFA" />
            <stop offset="100%" stopColor="#22D3EE" />
          </linearGradient>

          {/* Subtle inner glow for the background */}
          <radialGradient id={`${id}-glow`} cx="30%" cy="30%" r="60%">
            <stop offset="0%"   stopColor="#6366F1" stopOpacity="0.6" />
            <stop offset="100%" stopColor="#312E81" stopOpacity="0"   />
          </radialGradient>
        </defs>

        {/* Rounded-square background */}
        <rect width="48" height="48" rx="12" fill={`url(#${id}-bg)`} />
        {/* Inner glow overlay */}
        <rect width="48" height="48" rx="12" fill={`url(#${id}-glow)`} />

        {/*
          ── "P" letterform ──
          Vertical stem: (14,10) → (14,38)
          Bowl: semicircle from (14,10) via (14,26) out to x≈33, back to (14,26)
          Path: M14 38 V10 H25 A11 11 0 0 1 25 32 H14
        */}
        <path
          d="M14 38 V10 H25 A11 11 0 0 1 25 32 H14"
          stroke="white"
          strokeWidth="3.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          fill="none"
        />

        {/*
          ── Rising trend line ── sits below / right of the P bowl
          Four data points forming an upward step chart
        */}
        <path
          d="M17 38 L21 34 L26 35 L31 30 L36 26"
          stroke={`url(#${id}-accent)`}
          strokeWidth="2.2"
          strokeLinecap="round"
          strokeLinejoin="round"
          fill="none"
        />

        {/* Terminal node — glowing cyan dot at the top of the trend */}
        <circle cx="36" cy="26" r="2.5" fill="#22D3EE" />
        <circle cx="36" cy="26" r="4.5" fill="#22D3EE" fillOpacity="0.2" />
      </svg>

      {/* ── Wordmark ── */}
      {showText && (
        <div className="leading-tight select-none">
          <div className="text-white font-bold text-base tracking-wide">Prajna</div>
          {subtitle && (
            <div className="text-[10px] font-semibold tracking-[0.18em] uppercase"
              style={{ color: '#A78BFA' }}>
              {subtitle}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
