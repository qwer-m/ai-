import React, { useState } from 'react';
import { Form, Button, Card, Alert, Container } from 'react-bootstrap';
import { api } from '../utils/api';
import { useAuth } from '../contexts/AuthContext';
import { useNavigate, Link } from 'react-router-dom';

type TokenResponse = {
  access_token: string;
  token_type: string;
};

const Login: React.FC = () => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

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
      navigate('/');
    } catch (err: any) {
      setError(err.message || 'Failed to login');
      localStorage.removeItem('token');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Container className="d-flex align-items-center justify-content-center" style={{ minHeight: "100vh" }}>
      <div className="w-100" style={{ maxWidth: "400px" }}>
        <Card>
          <Card.Body>
            <h2 className="text-center mb-4">Login</h2>
            {error && <Alert variant="danger">{error}</Alert>}
            <Form onSubmit={handleSubmit}>
              <Form.Group id="username" className="mb-3">
                <Form.Label>Username</Form.Label>
                <Form.Control type="text" required value={username} onChange={e => setUsername(e.target.value)} />
              </Form.Group>
              <Form.Group id="password" className="mb-3">
                <Form.Label>Password</Form.Label>
                <Form.Control type="password" required value={password} onChange={e => setPassword(e.target.value)} />
              </Form.Group>
              <Button disabled={loading} className="w-100" type="submit">
                Login
              </Button>
            </Form>
            <div className="w-100 text-center mt-3">
              Need an account? <Link to="/register">Register</Link>
            </div>
          </Card.Body>
        </Card>
      </div>
    </Container>
  );
};

export default Login;
