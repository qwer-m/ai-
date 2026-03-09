export type IntroPhase = 'intro' | 'transition' | 'content';

interface IntroAnimationProps {
  phase: IntroPhase;
}

export function IntroAnimation({ phase }: IntroAnimationProps) {
  if (phase === 'content') {
    return null;
  }

  return (
    <div className={`login-intro login-intro--${phase}`} aria-hidden="true">
      <div className="login-intro__center">
        <div className="login-intro__logo">
          <span className="login-intro__wing login-intro__wing--left" />
          <span className="login-intro__wing login-intro__wing--right" />
        </div>
        <div className="login-intro__dots">
          <span />
          <span />
          <span />
        </div>
      </div>
    </div>
  );
}
