import React, { useState } from 'react';
import { Alert, Button, Card, Container, Form } from 'react-bootstrap';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../../utils/api';
import '../login/register-page.css';

const ForgotPassword: React.FC = () => {
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError('');
    setSuccess('');

    if (newPassword !== confirmPassword) {
      setError('两次输入的密码不一致');
      return;
    }
    if (newPassword.length < 6) {
      setError('密码至少需要 6 位');
      return;
    }

    setLoading(true);
    try {
      await api.post('/api/auth/password-reset', {
        username,
        email,
        new_password: newPassword,
      });
      setSuccess('密码已更新，可以使用新密码登录。');
      window.setTimeout(() => navigate('/login', { replace: true }), 900);
    } catch (err: any) {
      setError(err.message || '重置密码失败');
    } finally {
      setLoading(false);
    }
  };

  const hasError = Boolean(error);

  return (
    <Container className="d-flex align-items-center justify-content-center register-page-shell register-page-full-height">
      <div className="w-100 register-page-wrap register-page-wrap-narrow">
        <Card className="register-card">
          <Card.Body className="register-card-body">
            <h2 className="text-center mb-3">重置密码</h2>
            <p className="text-center text-muted mb-4">输入用户名和注册邮箱后设置新密码。</p>

            {error && (
              <Alert id="forgot-password-error" variant="danger" aria-live="polite">
                {error}
              </Alert>
            )}
            {success && (
              <Alert variant="success" aria-live="polite">
                {success}
              </Alert>
            )}

            <Form onSubmit={handleSubmit} aria-busy={loading}>
              <Form.Group controlId="forgot-username" className="mb-3">
                <Form.Label>用户名</Form.Label>
                <Form.Control
                  className="register-control"
                  type="text"
                  autoComplete="username"
                  required
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  aria-invalid={hasError}
                  aria-describedby={hasError ? 'forgot-password-error' : undefined}
                />
              </Form.Group>

              <Form.Group controlId="forgot-email" className="mb-3">
                <Form.Label>注册邮箱</Form.Label>
                <Form.Control
                  className="register-control"
                  type="email"
                  autoComplete="email"
                  required
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  aria-invalid={hasError}
                  aria-describedby={hasError ? 'forgot-password-error' : undefined}
                />
              </Form.Group>

              <Form.Group controlId="forgot-new-password" className="mb-3">
                <Form.Label>新密码</Form.Label>
                <Form.Control
                  className="register-control"
                  type="password"
                  autoComplete="new-password"
                  required
                  minLength={6}
                  value={newPassword}
                  onChange={(event) => setNewPassword(event.target.value)}
                  aria-invalid={hasError}
                  aria-describedby={hasError ? 'forgot-password-error' : undefined}
                />
              </Form.Group>

              <Form.Group controlId="forgot-confirm-password" className="mb-3">
                <Form.Label>确认新密码</Form.Label>
                <Form.Control
                  className="register-control"
                  type="password"
                  autoComplete="new-password"
                  required
                  minLength={6}
                  value={confirmPassword}
                  onChange={(event) => setConfirmPassword(event.target.value)}
                  aria-invalid={hasError}
                  aria-describedby={hasError ? 'forgot-password-error' : undefined}
                />
              </Form.Group>

              <Button disabled={loading} className="w-100 register-primary-btn" type="submit">
                {loading ? '重置中...' : '重置密码'}
              </Button>
            </Form>

            <div className="w-100 text-center mt-3">
              想起密码了？<Link to="/login">返回登录</Link>
            </div>
          </Card.Body>
        </Card>
      </div>
    </Container>
  );
};

export default ForgotPassword;
