import { useState } from 'react';
import { Button, Form, Card, Row, Col, Spinner, InputGroup } from 'react-bootstrap';
import { FaRobot, FaPaperPlane, FaCog, FaEraser } from 'react-icons/fa';

type AIModelTestingProps = {
    projectId: number | null;
    onLog: (msg: string) => void;
};

export function AIModelTesting({ onLog }: AIModelTestingProps) {
    const [systemPrompt, setSystemPrompt] = useState('');
    const [userPrompt, setUserPrompt] = useState('');
    const [model, setModel] = useState('qwen-plus');
    const [temperature, setTemperature] = useState(0.7);
    const [maxTokens, setMaxTokens] = useState(2000);
    const [loading, setLoading] = useState(false);
    const [response, setResponse] = useState('');

    const handleSend = async () => {
        if (!userPrompt.trim()) return;
        setLoading(true);
        setResponse(''); // Clear previous response
        
        try {
            // Placeholder for actual AI model call
            // In a real implementation, this would call a backend endpoint that streams the response
            // For now, we'll simulate a request or call a generic chat endpoint if available
            onLog('发送 AI 模型调试请求...');
            
            // Simulating stream for UI demonstration
            const demoResponse = "This is a simulated response from the AI model.\nIn a real implementation, this would be streamed from the backend.";
            let currentText = '';
            for (const char of demoResponse) {
                await new Promise(r => setTimeout(r, 50));
                currentText += char;
                setResponse(currentText);
            }
            
            onLog('AI 模型响应完成');
        } catch (e) {
            onLog(`请求失败: ${e}`);
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="d-flex h-100 w-100 bg-white overflow-hidden">
            {/* Left Column: Prompt Engineering */}
            <div className="d-flex flex-column border-end" style={{ width: '50%', minWidth: '300px' }}>
                <div className="p-3 border-bottom bg-light d-flex justify-content-between align-items-center">
                    <div className="fw-bold text-secondary"><FaRobot className="me-2"/>Prompt Engineering</div>
                    <Button variant="link" size="sm" className="text-muted text-decoration-none" onClick={() => {
                        setSystemPrompt(''); setUserPrompt('');
                    }}><FaEraser className="me-1"/>清空</Button>
                </div>
                <div className="flex-grow-1 overflow-auto p-3 d-flex flex-column gap-3">
                    <Form.Group className="flex-grow-1 d-flex flex-column">
                        <Form.Label className="small fw-bold text-muted">System Prompt (系统预设)</Form.Label>
                        <Form.Control 
                            as="textarea" 
                            className="flex-grow-1 font-monospace small bg-light" 
                            placeholder="You are a helpful assistant..."
                            value={systemPrompt}
                            onChange={e => setSystemPrompt(e.target.value)}
                            style={{ resize: 'none', minHeight: '150px' }}
                        />
                    </Form.Group>
                    <Form.Group className="flex-grow-1 d-flex flex-column">
                        <Form.Label className="small fw-bold text-muted">User Prompt (用户输入)</Form.Label>
                        <Form.Control 
                            as="textarea" 
                            className="flex-grow-1 font-monospace small" 
                            placeholder="Enter your query here..."
                            value={userPrompt}
                            onChange={e => setUserPrompt(e.target.value)}
                            style={{ resize: 'none', minHeight: '150px' }}
                        />
                    </Form.Group>
                </div>
                <div className="p-3 border-top bg-light">
                    <Button variant="primary" className="w-100" onClick={handleSend} disabled={loading || !userPrompt.trim()}>
                        {loading ? <Spinner size="sm" animation="border" /> : <><FaPaperPlane className="me-2"/>发送请求</>}
                    </Button>
                </div>
            </div>

            {/* Right Column: Model Config & Response */}
            <div className="d-flex flex-column flex-grow-1" style={{ minWidth: '300px' }}>
                <div className="p-3 border-bottom bg-light d-flex justify-content-between align-items-center">
                    <div className="fw-bold text-secondary"><FaCog className="me-2"/>Model Configuration</div>
                </div>
                
                {/* Configuration Panel */}
                <div className="p-3 border-bottom bg-white">
                    <Row className="g-2">
                        <Col md={12}>
                            <InputGroup size="sm">
                                <InputGroup.Text>Model</InputGroup.Text>
                                <Form.Select value={model} onChange={e => setModel(e.target.value)}>
                                    <option value="qwen-plus">Qwen Plus</option>
                                    <option value="qwen-turbo">Qwen Turbo</option>
                                    <option value="gpt-3.5-turbo">GPT-3.5 Turbo</option>
                                    <option value="gpt-4">GPT-4</option>
                                </Form.Select>
                            </InputGroup>
                        </Col>
                        <Col md={6}>
                            <InputGroup size="sm">
                                <InputGroup.Text>Temp</InputGroup.Text>
                                <Form.Control 
                                    type="number" 
                                    step="0.1" 
                                    min="0" 
                                    max="1" 
                                    value={temperature} 
                                    onChange={e => setTemperature(parseFloat(e.target.value))} 
                                />
                            </InputGroup>
                        </Col>
                        <Col md={6}>
                            <InputGroup size="sm">
                                <InputGroup.Text>Tokens</InputGroup.Text>
                                <Form.Control 
                                    type="number" 
                                    step="100" 
                                    min="100" 
                                    max="8000" 
                                    value={maxTokens} 
                                    onChange={e => setMaxTokens(parseInt(e.target.value))} 
                                />
                            </InputGroup>
                        </Col>
                    </Row>
                </div>

                {/* Response Area */}
                <div className="flex-grow-1 d-flex flex-column overflow-hidden bg-light p-3">
                    <div className="small fw-bold text-muted mb-2">Model Response</div>
                    <Card className="flex-grow-1 border-0 shadow-sm overflow-hidden">
                        <Card.Body className="p-3 overflow-auto font-monospace small bg-white">
                            {response ? (
                                <div style={{ whiteSpace: 'pre-wrap' }}>{response}</div>
                            ) : (
                                <div className="text-muted text-center mt-5">
                                    Waiting for response...
                                </div>
                            )}
                        </Card.Body>
                    </Card>
                </div>
            </div>
        </div>
    );
}
