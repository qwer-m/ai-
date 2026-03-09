import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { api } from '../utils/api';
import { useAuth } from '../contexts/AuthContext';
import { useNavigate } from 'react-router-dom';
import { IntroAnimation, type IntroPhase } from './login/IntroAnimation';
import { CharacterScene } from './login/CharacterScene';
import { LoginCard } from './login/LoginCard';
import { LoginFormPanel } from './login/LoginFormPanel';
import './login/login-page.css';

type TokenResponse = {
  access_token: string;
  token_type: string;
};

type FocusTarget = 'email' | 'password' | null;
type ButtonState = 'idle' | 'hover' | 'press';

const INTRO_DURATION_MS = 1400;
// Keep transition phase long enough for staged pane + character stagger to finish naturally.
const REVEAL_DURATION_MS = 1020;

const Login = () => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [remember, setRemember] = useState(true);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [focusTarget, setFocusTarget] = useState<FocusTarget>(null);
  const [introPhase, setIntroPhase] = useState<IntroPhase>('intro');
  const [buttonState, setButtonState] = useState<ButtonState>('idle');
  const { login } = useAuth();
  const navigate = useNavigate();
  const feedbackTimerRef = useRef<number | null>(null);

  useEffect(() => {
    const startTransition = window.setTimeout(() => {
      setIntroPhase('transition');
    }, INTRO_DURATION_MS);

    const endTransition = window.setTimeout(() => {
      setIntroPhase('content');
    }, INTRO_DURATION_MS + REVEAL_DURATION_MS);

    return () => {
      window.clearTimeout(startTransition);
      window.clearTimeout(endTransition);
    };
  }, []);

  useEffect(() => {
    return () => {
      if (feedbackTimerRef.current !== null) {
        window.clearTimeout(feedbackTimerRef.current);
      }
    };
  }, []);

  const handleSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    setButtonState('press');

    try {
      const formData = new FormData();
      formData.append('username', username);
      formData.append('password', password);

      const data = await api.upload<TokenResponse>('/api/auth/token', formData);
      if (!data || !data.access_token) {
        throw new Error('Login failed');
      }
      const token = data.access_token;
      
      // Store token first so api.get uses it
      localStorage.setItem('token', token);
      
      // Get user details
      const user = await api.get<any>('/api/auth/me');
      
      login(token, user);
      navigate('/', { replace: true });
    } catch (err: any) {
      setError(err.message || 'Failed to login');
      localStorage.removeItem('token');
    } finally {
      setLoading(false);
      if (feedbackTimerRef.current !== null) {
        window.clearTimeout(feedbackTimerRef.current);
      }
      feedbackTimerRef.current = window.setTimeout(() => {
        setButtonState('idle');
      }, 220);
    }
  };

  const handleButtonHover = (isHovering: boolean) => {
    if (loading) {
      return;
    }
    setButtonState(isHovering ? 'hover' : 'idle');
  };

  return (
    <div className="login-page">
      <IntroAnimation phase={introPhase} />
      <div className="login-page__glow" aria-hidden="true" />
      <main className="login-page__main">
        <LoginCard
          introPhase={introPhase}
          scene={(
            <CharacterScene
              focusTarget={focusTarget}
              buttonState={buttonState}
              isSubmitting={loading}
            />
          )}
          form={(
            <LoginFormPanel
              username={username}
              password={password}
              remember={remember}
              error={error}
              loading={loading}
              buttonState={buttonState}
              onUsernameChange={setUsername}
              onPasswordChange={setPassword}
              onRememberChange={setRemember}
              onSubmit={handleSubmit}
              onEmailFocus={() => setFocusTarget('email')}
              onPasswordFocus={() => setFocusTarget('password')}
              onFieldBlur={() => setFocusTarget(null)}
              onPrimaryButtonHoverChange={handleButtonHover}
            />
          )}
        />
      </main>
      <div className="login-page__decor login-page__decor--top" aria-hidden="true" />
      <div className="login-page__decor login-page__decor--bottom" aria-hidden="true" />
      <div className="login-page__decor login-page__decor--side" aria-hidden="true" />
      <div className="login-page__sr-only" aria-live="polite">
        {remember ? 'Remember for 30 days enabled' : 'Remember for 30 days disabled'}
      </div>
    </div>
  );
};

export default Login;
