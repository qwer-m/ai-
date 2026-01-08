import React, { useState } from 'react';
import { Form, Button, Card, Alert, Container } from 'react-bootstrap';
import { api } from '../utils/api';
import { useNavigate, Link } from 'react-router-dom';

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
        return setError('Passwords do not match');
    }
    
    setError('');
    setLoading(true);

    try {
      await api.post('/api/auth/register', {
        username,
        email,
        password
      });
      
      navigate('/login');
    } catch (err: any) {
      setError(err.message || 'Failed to register');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Container className="d-flex align-items-center justify-content-center" style={{ minHeight: "100vh" }}>
      <div className="w-100" style={{ maxWidth: "400px" }}>
        <Card>
          <Card.Body>
            <h2 className="text-center mb-4">Register</h2>
            {error && <Alert variant="danger">{error}</Alert>}
            <Form onSubmit={handleSubmit}>
              <Form.Group id="username" className="mb-3">
                <Form.Label>Username</Form.Label>
                <Form.Control type="text" required value={username} onChange={e => setUsername(e.target.value)} />
              </Form.Group>
              <Form.Group id="email" className="mb-3">
                <Form.Label>Email</Form.Label>
                <Form.Control type="email" required value={email} onChange={e => setEmail(e.target.value)} />
              </Form.Group>
              <Form.Group id="password" className="mb-3">
                <Form.Label>Password</Form.Label>
                <Form.Control type="password" required value={password} onChange={e => setPassword(e.target.value)} />
              </Form.Group>
              <Form.Group id="confirm-password" className="mb-3">
                <Form.Label>Confirm Password</Form.Label>
                <Form.Control type="password" required value={confirmPassword} onChange={e => setConfirmPassword(e.target.value)} />
              </Form.Group>
              <Button disabled={loading} className="w-100" type="submit">
                Register
              </Button>
            </Form>
            <div className="w-100 text-center mt-3">
              Already have an account? <Link to="/login">Login</Link>
            </div>
          </Card.Body>
        </Card>
      </div>
    </Container>
  );
};

export default Register;
