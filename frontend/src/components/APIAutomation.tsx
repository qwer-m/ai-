import { useState } from 'react';
import { Button, Form, Card, Table, Nav, Badge, Modal } from 'react-bootstrap';
import { FaPlay, FaPlus, FaTrash, FaEdit, FaLayerGroup } from 'react-icons/fa';

type APIAutomationProps = {
    projectId: number | null;
    onLog: (msg: string) => void;
    view?: 'orchestration' | 'runner';
};

type TestScene = {
    id: number;
    name: string;
    description: string;
    steps: number;
    lastRun?: string;
    status?: 'success' | 'failed' | 'running';
};

export function APIAutomation({ onLog, view }: APIAutomationProps) {
    const [internalTab, setInternalTab] = useState<'orchestration' | 'runner'>('orchestration');
    const activeTab = view || internalTab;

    const [scenes] = useState<TestScene[]>([
        { id: 1, name: '用户注册登录流程', description: '注册新用户 -> 登录获取Token -> 获取用户信息', steps: 3, lastRun: '2023-10-27 10:00', status: 'success' },
        { id: 2, name: '订单创建支付流程', description: '添加商品 -> 创建订单 -> 模拟支付 -> 检查状态', steps: 4, lastRun: '2023-10-27 10:05', status: 'failed' },
    ]);
    const [showModal, setShowModal] = useState(false);

    const handleRunScene = (id: number) => {
        onLog(`开始执行测试场景 #${id}...`);
        // Simulate running
        setTimeout(() => {
            onLog(`测试场景 #${id} 执行完成`);
        }, 2000);
    };

    return (
        <div className="d-flex flex-column h-100 w-100 bg-white">
            {!view && (
                <div className="border-bottom bg-light px-3 pt-2">
                    <Nav variant="tabs" activeKey={activeTab} onSelect={(k) => setInternalTab(k as 'orchestration' | 'runner')}>
                        <Nav.Item>
                            <Nav.Link eventKey="orchestration" className="d-flex align-items-center gap-2">
                                <FaLayerGroup /> 自动化编排 (Orchestration)
                            </Nav.Link>
                        </Nav.Item>
                        <Nav.Item>
                            <Nav.Link eventKey="runner" className="d-flex align-items-center gap-2">
                                <FaPlay /> 批量运行 (Batch Runner)
                            </Nav.Link>
                        </Nav.Item>
                    </Nav>
                </div>
            )}

            <div className="flex-grow-1 overflow-auto p-3">
                {activeTab === 'orchestration' ? (
                    <div className="d-flex flex-column gap-3">
                        <div className="d-flex justify-content-between align-items-center">
                            <h5 className="mb-0 text-secondary">测试场景管理</h5>
                            <Button variant="primary" size="sm" onClick={() => setShowModal(true)}>
                                <FaPlus className="me-1"/> 新建场景
                            </Button>
                        </div>
                        <Card className="border-0 shadow-sm">
                            <Table hover responsive className="mb-0 align-middle">
                                <thead className="bg-light">
                                    <tr>
                                        <th>ID</th>
                                        <th>场景名称</th>
                                        <th>描述</th>
                                        <th>步骤数</th>
                                        <th>操作</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {scenes.map(scene => (
                                        <tr key={scene.id}>
                                            <td>#{scene.id}</td>
                                            <td className="fw-medium">{scene.name}</td>
                                            <td className="text-muted small">{scene.description}</td>
                                            <td><Badge bg="secondary">{scene.steps} 步骤</Badge></td>
                                            <td>
                                                <Button variant="link" size="sm" className="p-0 me-3" title="编辑"><FaEdit /></Button>
                                                <Button variant="link" size="sm" className="p-0 text-danger" title="删除"><FaTrash /></Button>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </Table>
                        </Card>
                    </div>
                ) : (
                    <div className="d-flex flex-column gap-3">
                        <div className="d-flex justify-content-between align-items-center">
                            <h5 className="mb-0 text-secondary">批量运行任务</h5>
                            <Button variant="success" size="sm">
                                <FaPlay className="me-1"/> 运行选中场景
                            </Button>
                        </div>
                        <Card className="border-0 shadow-sm">
                            <Table hover responsive className="mb-0 align-middle">
                                <thead className="bg-light">
                                    <tr>
                                        <th style={{width: '40px'}}><Form.Check /></th>
                                        <th>场景名称</th>
                                        <th>上次运行</th>
                                        <th>状态</th>
                                        <th>操作</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {scenes.map(scene => (
                                        <tr key={scene.id}>
                                            <td><Form.Check /></td>
                                            <td className="fw-medium">{scene.name}</td>
                                            <td className="small text-muted">{scene.lastRun}</td>
                                            <td>
                                                <Badge bg={scene.status === 'success' ? 'success' : scene.status === 'failed' ? 'danger' : 'warning'}>
                                                    {scene.status?.toUpperCase()}
                                                </Badge>
                                            </td>
                                            <td>
                                                <Button variant="outline-primary" size="sm" onClick={() => handleRunScene(scene.id)}>
                                                    <FaPlay size={10} className="me-1"/> 运行
                                                </Button>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </Table>
                        </Card>
                    </div>
                )}
            </div>

            {/* Mock Modal for Create Scene */}
            <Modal show={showModal} onHide={() => setShowModal(false)}>
                <Modal.Header closeButton>
                    <Modal.Title>新建测试场景</Modal.Title>
                </Modal.Header>
                <Modal.Body>
                    <Form>
                        <Form.Group className="mb-3">
                            <Form.Label>场景名称</Form.Label>
                            <Form.Control type="text" placeholder="例如：用户下单全流程" />
                        </Form.Group>
                        <Form.Group className="mb-3">
                            <Form.Label>描述</Form.Label>
                            <Form.Control as="textarea" rows={3} />
                        </Form.Group>
                    </Form>
                </Modal.Body>
                <Modal.Footer>
                    <Button variant="secondary" onClick={() => setShowModal(false)}>取消</Button>
                    <Button variant="primary" onClick={() => setShowModal(false)}>创建</Button>
                </Modal.Footer>
            </Modal>
        </div>
    );
}
