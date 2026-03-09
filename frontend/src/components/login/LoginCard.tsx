import type { IntroPhase } from './IntroAnimation';
import type { ReactNode } from 'react';

interface LoginCardProps {
  introPhase: IntroPhase;
  scene: ReactNode;
  form: ReactNode;
}

export function LoginCard({ introPhase, scene, form }: LoginCardProps) {
  // Keep one stable class after reveal starts so CSS animations are not reset by phase toggles.
  const cardStateClass = introPhase === 'intro' ? '' : 'login-card-shell--visible';

  return (
    <section className={`login-card-shell ${cardStateClass}`.trim()}>
      <div className="login-card">
        <section className="login-card__scene-pane">{scene}</section>
        <section className="login-card__form-pane">{form}</section>
      </div>
    </section>
  );
}
