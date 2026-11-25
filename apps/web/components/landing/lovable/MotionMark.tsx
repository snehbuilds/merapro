export function MotionMark({ className = "" }: { className?: string }) {
  return (
    <svg
      viewBox="82 132 576 365"
      className={className}
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <linearGradient id="talonic-motion-wing" x1="0" y1="0" x2="1" y2=".45">
          <stop offset="0" stopColor="#12e5ed" />
          <stop offset=".42" stopColor="#008dff" />
          <stop offset="1" stopColor="#2631e9" />
        </linearGradient>
        <linearGradient id="talonic-motion-tail" x1="0" y1=".3" x2="1" y2=".4">
          <stop offset="0" stopColor="#04ceec" />
          <stop offset=".52" stopColor="#0788f8" />
          <stop offset="1" stopColor="#2521d8" />
        </linearGradient>
      </defs>

      <g className="talonic-motion-logo">
        <path
          className="talonic-motion-fill talonic-motion-fill-top"
          fill="url(#talonic-motion-wing)"
          d="M100 148H630Q641 148 635 159L605 185Q580 206 548 207H245Q157 207 124 170Z"
        />
        <path
          className="talonic-motion-fill talonic-motion-fill-mid"
          fill="url(#talonic-motion-wing)"
          d="M115 243H394Q365 263 338 294H240Q155 294 126 261Z"
        />
        <path
          className="talonic-motion-fill talonic-motion-fill-tail"
          fill="url(#talonic-motion-tail)"
          d="M553 207Q472 226 422 304Q390 349 363 411Q345 449 310 461L257 485Q293 409 324 344Q363 263 444 230Q498 207 553 207Z"
        />

        <path
          className="talonic-motion-line talonic-motion-line-top"
          d="M100 148H630Q641 148 635 159L605 185Q580 206 548 207H245Q157 207 124 170Z"
        />
        <path
          className="talonic-motion-line talonic-motion-line-mid"
          d="M115 243H394Q365 263 338 294H240Q155 294 126 261Z"
        />
        <path
          className="talonic-motion-line talonic-motion-line-tail"
          d="M553 207Q472 226 422 304Q390 349 363 411Q345 449 310 461L257 485Q293 409 324 344Q363 263 444 230Q498 207 553 207Z"
        />
      </g>
    </svg>
  );
}
