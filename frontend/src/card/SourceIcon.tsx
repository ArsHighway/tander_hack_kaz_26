/**
 * Значок «документ с i» из UI-кита портала (Figma организаторов, 02_Icons, узел 1:60628 «icon/history»): откуда
 * взято значение. Пути — из экспорта SVG без изменений, цвет — currentColor.
 */
export function SourceIcon({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 30 30" fill="none" aria-hidden focusable="false">
      <path
        d="M23 13V5C23 3.89543 22.1046 3 21 3H7C5.89543 3 5 3.89543 5 5V23C5 24.1046 5.89543 25 7 25H12"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <line x1="10" y1="8" x2="18" y2="8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <line x1="10" y1="12" x2="18" y2="12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M10 16H14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="1" cy="1" r="1" transform="matrix(-1 0 0 1 22 18)" fill="currentColor" />
      <rect x="20" y="21" width="2" height="5" rx="1" fill="currentColor" />
      <circle cx="21" cy="22" r="6" stroke="currentColor" strokeWidth="2" />
    </svg>
  )
}
