type FocusTarget = 'email' | 'password' | null;
type ButtonState = 'idle' | 'hover' | 'press';

interface CharacterSceneProps {
  focusTarget: FocusTarget;
  buttonState: ButtonState;
  isSubmitting: boolean;
}

export function CharacterScene({ focusTarget, buttonState, isSubmitting }: CharacterSceneProps) {
  const focusClass =
    focusTarget === 'email'
      ? 'character-scene--email'
      : focusTarget === 'password'
        ? 'character-scene--password'
        : 'character-scene--idle';

  const buttonClass =
    buttonState === 'hover'
      ? 'character-scene--button-hover'
      : buttonState === 'press'
        ? 'character-scene--button-press'
        : '';

  return (
    <div
      className={`character-scene ${focusClass} ${buttonClass} ${isSubmitting ? 'character-scene--submitting' : ''}`.trim()}
      aria-hidden="true"
    >
      <div className="character geo-purple">
        <div className="character__eyes">
          <span className="character__eye" />
          <span className="character__eye" />
        </div>
        <span className="character__mouth character__mouth--tiny" />
      </div>

      <div className="character geo-black">
        <div className="character__eyes">
          <span className="character__eye" />
          <span className="character__eye" />
        </div>
      </div>

      <div className="character geo-orange">
        <div className="character__eyes">
          <span className="character__eye" />
          <span className="character__eye" />
        </div>
        <span className="character__mouth character__mouth--flat" />
      </div>

      <div className="character geo-yellow">
        <div className="character__eyes character__eyes--single">
          <span className="character__eye" />
        </div>
        <span className="character__mouth character__mouth--line" />
      </div>
    </div>
  );
}

