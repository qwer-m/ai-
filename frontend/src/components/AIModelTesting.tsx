import { useState } from 'react';
import { api } from '../utils/api';
import { Button, Form, Card, Row, Col, Spinner, InputGroup } from 'react-bootstrap';
import { FaRobot, FaPaperPlane, FaCog, FaEraser } from 'react-icons/fa';

type AIModelTestingProps = {
  projectId: number | null;
  onLog: (msg: string) => void;
};

export function AIModelTesting({ onLog }: AIModelTestingProps) {
  const [systemPrompt, setSystemPrompt] = useState('');
  const [userPrompt, setUserPrompt] = useState('');
  const [model, setModel] = useState('');
  const [temperature, setTemperature] = useState(0.7);
  const [maxTokens, setMaxTokens] = useState(2000);
  const [loading, setLoading] = useState(false);
  const [response, setResponse] = useState('');

  const getErrorText = (error: any) => {
    if (!error) return '';
    if (typeof error === 'string') return error;
    if (error?.data?.error) return String(error.data.error);
    if (error?.data?.detail) return String(error.data.detail);
    if (error?.data?.message) return String(error.data.message);
    if (error?.message) return String(error.message);
    try {
      return JSON.stringify(error);
    } catch {
      return String(error);
    }
  };

  const translateError = async (error: any) => {
    const raw = getErrorText(error);
    try {
      const res = await api.post<any>('/api/error/translate', { error: raw });
      return res?.message ? String(res.message) : raw;
    } catch {
      return raw;
    }
  };

  const handleSend = async () => {
    if (!userPrompt.trim()) return;
    setLoading(true);
    setResponse('');
    try {
      onLog('发送 AI 模型调试请求...');

      const demoResponse = '这是 AI 模型的模拟响应。\n在实际实现中，这里会从后端进行流式返回。';
      let currentText = '';
      for (const char of demoResponse) {
        await new Promise((r) => setTimeout(r, 50));
        currentText += char;
        setResponse(currentText);
      }

      onLog('AI 模型响应完成');
    } catch (e) {
      const msg = await translateError(e);
      onLog(`请求失败: ${msg}`);
      setResponse(msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="d-flex h-100 w-100 bg-white overflow-hidden">
      <div className="d-flex flex-column border-end" style={{ width: '50%', minWidth: '300px' }}>
        <div className="p-3 border-bottom bg-light d-flex justify-content-between align-items-center">
          <div className="fw-bold text-secondary"><FaRobot className="me-2" />提示词工程</div>
          <Button
            variant="link"
            size="sm"
            className="text-muted text-decoration-none"
            onClick={() => {
              setSystemPrompt('');
              setUserPrompt('');
            }}
          >
            <FaEraser className="me-1" />清空
          </Button>
        </div>

        <div className="flex-grow-1 overflow-auto p-3 d-flex flex-column gap-3">
          <Form.Group className="flex-grow-1 d-flex flex-column">
            <Form.Label className="small fw-bold text-muted">系统提示词</Form.Label>
            <Form.Control
              as="textarea"
              className="flex-grow-1 font-monospace small bg-light"
              placeholder="你是一个乐于助人的助手..."
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              style={{ resize: 'none', minHeight: '150px' }}
            />
          </Form.Group>

          <Form.Group className="flex-grow-1 d-flex flex-column">
            <Form.Label className="small fw-bold text-muted">用户提示词</Form.Label>
            <Form.Control
              as="textarea"
              className="flex-grow-1 font-monospace small"
              placeholder="请输入你的问题..."
              value={userPrompt}
              onChange={(e) => setUserPrompt(e.target.value)}
              style={{ resize: 'none', minHeight: '150px' }}
            />
          </Form.Group>
        </div>

        <div className="p-3 border-top bg-light">
          <Button variant="primary" className="w-100" onClick={handleSend} disabled={loading || !userPrompt.trim()}>
            {loading ? <Spinner size="sm" animation="border" /> : <><FaPaperPlane className="me-2" />发送请求</>}
          </Button>
        </div>
      </div>

      <div className="d-flex flex-column flex-grow-1" style={{ minWidth: '300px' }}>
        <div className="p-3 border-bottom bg-light d-flex justify-content-between align-items-center">
          <div className="fw-bold text-secondary"><FaCog className="me-2" />模型配置</div>
        </div>

        <div className="p-3 border-bottom bg-white">
          <Row className="g-2">
            <Col md={12}>
              <InputGroup size="sm">
                <InputGroup.Text>模型</InputGroup.Text>
                <Form.Control
                  type="text"
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  list="testing-models"
                  placeholder="例如：qwen-plus"
                />
                <datalist id="testing-models" />
              </InputGroup>
            </Col>
            <Col md={6}>
              <InputGroup size="sm">
                <InputGroup.Text>温度</InputGroup.Text>
                <Form.Control
                  type="number"
                  step="0.1"
                  min="0"
                  max="1"
                  value={temperature}
                  onChange={(e) => setTemperature(parseFloat(e.target.value))}
                />
              </InputGroup>
            </Col>
            <Col md={6}>
              <InputGroup size="sm">
                <InputGroup.Text>令牌上限</InputGroup.Text>
                <Form.Control
                  type="number"
                  step="100"
                  min="100"
                  max="8000"
                  value={maxTokens}
                  onChange={(e) => setMaxTokens(parseInt(e.target.value, 10))}
                />
              </InputGroup>
            </Col>
          </Row>
        </div>

        <div className="flex-grow-1 d-flex flex-column overflow-hidden bg-light p-3">
          <div className="small fw-bold text-muted mb-2">模型响应</div>
          <Card className="flex-grow-1 border-0 shadow-sm overflow-hidden">
            <Card.Body className="p-3 overflow-auto font-monospace small bg-white">
              {response ? (
                <div style={{ whiteSpace: 'pre-wrap' }}>{response}</div>
              ) : (
                <div className="text-muted text-center mt-5">等待模型响应...</div>
              )}
            </Card.Body>
          </Card>
        </div>
      </div>
    </div>
  );
}
