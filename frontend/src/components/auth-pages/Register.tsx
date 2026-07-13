import React, { useState } from 'react';
import { Form, Button, Card, Alert, Container } from 'react-bootstrap';
import { api } from '../../utils/api';
import { useNavigate, Link } from 'react-router-dom';
import '../login/register-page.css';

const Register: React.FC = () => {
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (password !== confirmPassword) {
      return setError('两次输入的密码不一致');
    }
    if (password.length < 6) {
      return setError('密码至少需要 6 位');
    }

    setError('');
    setLoading(true);

    try {
      await api.post('/api/auth/register', {
        username,
        email,
        password,
      });

      navigate('/login', { replace: true });
    } catch (err: any) {
      setError(err.message || '注册失败');
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
            <h2 className="text-center mb-4">注册账号</h2>
            {error && (
              <Alert id="register-error" variant="danger" aria-live="polite">
                {error}
              </Alert>
            )}
            <Form onSubmit={handleSubmit} aria-busy={loading}>
              <Form.Group controlId="username" className="mb-3">
                <Form.Label>用户名</Form.Label>
                <Form.Control
                  className="register-control"
                  type="text"
                  autoComplete="username"
                  required
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  aria-invalid={hasError}
                  aria-describedby={hasError ? 'register-error' : undefined}
                />
              </Form.Group>
              <Form.Group controlId="email" className="mb-3">
                <Form.Label>Email</Form.Label>
                <Form.Control
                  className="register-control"
                  type="email"
                  autoComplete="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  aria-invalid={hasError}
                  aria-describedby={hasError ? 'register-error' : undefined}
                />
              </Form.Group>
              <Form.Group controlId="password" className="mb-3">
                <Form.Label>密码</Form.Label>
                <Form.Control
                  className="register-control"
                  type="password"
                  autoComplete="new-password"
                  required
                  minLength={6}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  aria-invalid={hasError}
                  aria-describedby={hasError ? 'register-error' : undefined}
                />
              </Form.Group>
              <Form.Group controlId="confirm-password" className="mb-3">
                <Form.Label>确认密码</Form.Label>
                <Form.Control
                  className="register-control"
                  type="password"
                  autoComplete="new-password"
                  required
                  minLength={6}
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  aria-invalid={hasError}
                  aria-describedby={hasError ? 'register-error' : undefined}
                />
              </Form.Group>
              <Button disabled={loading} className="w-100 register-primary-btn" type="submit">
                {loading ? '注册中...' : '注册'}
              </Button>
            </Form>
            <div className="w-100 text-center mt-3">
              已有账号？<Link to="/login">登录</Link>
            </div>
          </Card.Body>
        </Card>
      </div>
    </Container>
  );
};

export default Register;
