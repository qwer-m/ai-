import { useState } from 'react';
import { Button, Form, Spinner } from 'react-bootstrap';
import { api } from '../utils/api';

type Props = {
  projectId: number | null;
  onLog: (msg: string) => void;
};

export function UIAutomation({ projectId, onLog }: Props) {
  const [url, setUrl] = useState('');
  const [task, setTask] = useState('');
  const [type, setType] = useState('web');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  const handleRun = async () => {
    if (!projectId) return alert('请先选择项目');
    if (!url || !task) return alert('请输入 URL 和任务描述');
    
    setLoading(true);
    setResult(null);
    onLog(`开始执行 UI 自动化: ${type}...`);
    
    try {
      const data = await api.post<any>('/api/ui-automation', { 
        url, 
        task, 
        automation_type: type, 
        project_id: projectId 
      });
      
      setResult(JSON.stringify(data, null, 2));
      onLog('UI 自动化执行完成');
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setResult(`Error: ${msg}`);
      onLog(`UI 自动化失败: ${msg}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="h-100 d-flex flex-column">
      <div className="border rounded p-3 bg-light mb-3">
        <Form.Group className="mb-3">
          <Form.Label>目标 URL / 应用包名</Form.Label>
          <Form.Control value={url} onChange={e => setUrl(e.target.value)} placeholder="https://example.com" />
        </Form.Group>
        <Form.Group className="mb-3">
          <Form.Label>任务描述</Form.Label>
          <Form.Control as="textarea" rows={3} value={task} onChange={e => setTask(e.target.value)} placeholder="例如：登录系统，用户名admin，密码123456" />
        </Form.Group>
        <div className="d-flex gap-3 align-items-end">
          <Form.Group className="flex-grow-1">
            <Form.Label>自动化类型</Form.Label>
            <Form.Select value={type} onChange={e => setType(e.target.value)}>
              <option value="web">Web (Selenium)</option>
              <option value="mobile">Mobile (Appium)</option>
              <option value="desktop">Desktop (PyAutoGUI)</option>
            </Form.Select>
          </Form.Group>
          <Button variant="primary" onClick={handleRun} disabled={loading || !projectId}>
            {loading ? <Spinner size="sm" animation="border" /> : '开始执行'}
          </Button>
        </div>
      </div>
      
      <div className="flex-grow-1 d-flex flex-column" style={{ minHeight: 0 }}>
        <h5 className="mb-2">执行结果</h5>
        <div className="flex-grow-1 border rounded p-3 bg-light overflow-auto" style={{ fontFamily: 'monospace', whiteSpace: 'pre-wrap' }}>
          {result || <div className="text-muted text-center mt-5">暂无结果</div>}
        </div>
      </div>
    </div>
  );
}
