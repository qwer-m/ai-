import { useState } from 'react';
import { Nav } from 'react-bootstrap';
import { FaCode, FaRobot } from 'react-icons/fa';
import { StandardAPITesting } from './StandardAPITesting';
import { AIModelTesting } from './AIModelTesting';

type APITestingProps = {
  projectId: number | null;
  onLog: (msg: string) => void;
  view?: 'standard' | 'ai_debug';
};

export function APITesting({ projectId, onLog, view }: APITestingProps) {
  const [internalTab, setInternalTab] = useState<'standard' | 'ai_debug'>('standard');
  const activeTab = view || internalTab;

  return (
    <div className="d-flex flex-column h-100 w-100 bg-white">
      {/* Top Navigation Tabs - Only show if no external view control */}
      {!view && (
        <div className="border-bottom bg-light px-3 pt-2">
          <Nav variant="tabs" activeKey={activeTab} onSelect={(k) => setInternalTab(k as 'standard' | 'ai_debug')}>
            <Nav.Item>
              <Nav.Link eventKey="standard" className="d-flex align-items-center gap-2">
                <FaCode /> 标准接口测试 (Standard)
              </Nav.Link>
            </Nav.Item>
            <Nav.Item>
              <Nav.Link eventKey="ai_debug" className="d-flex align-items-center gap-2">
                <FaRobot /> AI 模型调试 (Debug)
              </Nav.Link>
            </Nav.Item>
          </Nav>
        </div>
      )}

      {/* Content Area */}
      <div className="flex-grow-1 overflow-hidden">
        {activeTab === 'standard' ? (
          <StandardAPITesting projectId={projectId} onLog={onLog} />
        ) : (
          <AIModelTesting projectId={projectId} onLog={onLog} />
        )}
      </div>
    </div>
  );
}
