import { Alert, Button, Form } from 'react-bootstrap';
import { Link } from 'react-router-dom';
import type { FormEvent } from 'react';

type ButtonState = 'idle' | 'hover' | 'press';

interface LoginFormPanelProps {
  username: string;
  password: string;
  remember: boolean;
  error: string;
  loading: boolean;
  googleLoading: boolean;
  buttonState: ButtonState;
  onUsernameChange: (nextValue: string) => void;
  onPasswordChange: (nextValue: string) => void;
  onRememberChange: (nextValue: boolean) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onForgotPassword: () => void;
  onGoogleLogin: () => void;
  onEmailFocus: () => void;
  onPasswordFocus: () => void;
  onFieldBlur: () => void;
  onPrimaryButtonHoverChange: (isHovering: boolean) => void;
}

export function LoginFormPanel({
  username,
  password,
  remember,
  error,
  loading,
  googleLoading,
  buttonState,
  onUsernameChange,
  onPasswordChange,
  onRememberChange,
  onSubmit,
  onForgotPassword,
  onGoogleLogin,
  onEmailFocus,
  onPasswordFocus,
  onFieldBlur,
  onPrimaryButtonHoverChange,
}: LoginFormPanelProps) {
  return (
    <div className="login-form-panel">
      <div className="login-brand" aria-hidden="true">
        <span className="login-brand__dot login-brand__dot--left" />
        <span className="login-brand__dot login-brand__dot--right" />
      </div>

      <header className="login-form-panel__header">
        <h1>Welcome back!</h1>
        <p>Please enter your details</p>
      </header>

      {error && (
        <Alert id="login-error" variant="danger" className="login-alert" aria-live="polite">
          {error}
        </Alert>
      )}

      <Form onSubmit={onSubmit} className="login-form" noValidate>
        <Form.Group controlId="username" className="login-form__group">
          <Form.Label>Email</Form.Label>
          <Form.Control
            type="text"
            inputMode="email"
            autoComplete="username"
            placeholder="anna@gmail.com"
            required
            value={username}
            onChange={(event) => onUsernameChange(event.target.value)}
            onFocus={onEmailFocus}
            onBlur={onFieldBlur}
            aria-invalid={Boolean(error)}
            aria-describedby={error ? 'login-error' : undefined}
          />
        </Form.Group>

        <Form.Group controlId="password" className="login-form__group">
          <Form.Label>Password</Form.Label>
          <Form.Control
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => onPasswordChange(event.target.value)}
            onFocus={onPasswordFocus}
            onBlur={onFieldBlur}
            aria-invalid={Boolean(error)}
            aria-describedby={error ? 'login-error' : undefined}
          />
        </Form.Group>

        <div className="login-form__meta">
          <Form.Check
            id="remember-me"
            type="checkbox"
            label="Remember for 30 days"
            checked={remember}
            onChange={(event) => onRememberChange(event.target.checked)}
          />
          <button
            type="button"
            className="login-link-button"
            onClick={onForgotPassword}
            onMouseDown={(event) => event.preventDefault()}
          >
            Forgot password?
          </button>
        </div>

        <Button
          type="submit"
          disabled={loading}
          className={`login-primary-btn ${buttonState === 'press' ? 'is-pressed' : ''}`.trim()}
          onMouseEnter={() => onPrimaryButtonHoverChange(true)}
          onMouseLeave={() => onPrimaryButtonHoverChange(false)}
          onFocus={() => onPrimaryButtonHoverChange(true)}
          onBlur={() => onPrimaryButtonHoverChange(false)}
        >
          {loading ? 'Logging in...' : 'Log In'}
        </Button>

        <Button
          type="button"
          variant="light"
          className="login-google-btn"
          disabled={googleLoading}
          onClick={onGoogleLogin}
        >
          <span className="login-google-btn__icon" aria-hidden="true">
            G
          </span>
          <span>{googleLoading ? 'Connecting Google...' : 'Log in with Google'}</span>
        </Button>
      </Form>

      <p className="login-form-panel__footnote">
        Don&apos;t have an account? <Link to="/register">Sign Up</Link>
      </p>
    </div>
  );
}

