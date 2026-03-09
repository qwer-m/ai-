import { Button, Form, ListGroup, Modal } from "react-bootstrap";
import { FaChevronLeft, FaEdit, FaPlus, FaTimes, FaTrash } from "react-icons/fa";
import type { Dispatch, SetStateAction } from "react";
import type { EnvConfig } from "./types";

type EnvManagerModalProps = {
  show: boolean;
  onHide: () => void;
  editingEnv: EnvConfig | null;
  setEditingEnv: Dispatch<SetStateAction<EnvConfig | null>>;
  savedEnvs: EnvConfig[];
  onDeleteEnv: (id: string) => void;
  onUpdateEnv: (env: EnvConfig) => void;
};

export function EnvManagerModal({
  show,
  onHide,
  editingEnv,
  setEditingEnv,
  savedEnvs,
  onDeleteEnv,
  onUpdateEnv,
}: EnvManagerModalProps) {
  // 环境管理弹窗有两种视图：
  // 1) 列表视图：管理已有环境
  // 2) 编辑视图：维护 baseUrl 和变量
  return (
    <Modal show={show} onHide={onHide} size="lg" centered>
      <Modal.Header closeButton>
        <Modal.Title>环境管理</Modal.Title>
      </Modal.Header>
      <Modal.Body style={{ minHeight: "400px" }}>
        {editingEnv ? (
          <div className="d-flex flex-column h-100">
            <div className="d-flex justify-content-between align-items-center mb-3">
              <Button
                variant="link"
                className="p-0 text-decoration-none"
                onClick={() => setEditingEnv(null)}
              >
                <FaChevronLeft className="me-1" />
                返回列表
              </Button>
              <h5 className="mb-0 text-primary">
                {editingEnv.id === "new" ? "新建环境" : "编辑环境"}
              </h5>
            </div>

            <Form.Group className="mb-3">
              <Form.Label>环境名称</Form.Label>
              <Form.Control
                value={editingEnv.name}
                onChange={(e) => setEditingEnv({ ...editingEnv, name: e.target.value })}
                placeholder="例如：生产环境"
              />
            </Form.Group>

            <Form.Group className="mb-3">
              <Form.Label>基础 URL</Form.Label>
              <Form.Control
                value={editingEnv.baseUrl}
                onChange={(e) => setEditingEnv({ ...editingEnv, baseUrl: e.target.value })}
                placeholder="https://api.example.com"
              />
            </Form.Group>

            <div className="flex-grow-1 d-flex flex-column">
              <div className="d-flex justify-content-between align-items-center mb-2">
                <label className="form-label mb-0">环境变量</label>
                <Button
                  size="sm"
                  variant="outline-secondary"
                  onClick={() => {
                    const nextVars = [
                      ...(editingEnv.variables || []),
                      { key: "", value: "", enabled: true },
                    ];
                    setEditingEnv({ ...editingEnv, variables: nextVars });
                  }}
                >
                  <FaPlus size={12} className="me-1" />
                  添加变量
                </Button>
              </div>

              <div
                className="border rounded flex-grow-1 overflow-auto bg-light p-2"
                style={{ maxHeight: "300px" }}
              >
                {(!editingEnv.variables || editingEnv.variables.length === 0) && (
                  <div className="text-center text-muted small mt-4">暂无变量定义</div>
                )}
                {editingEnv.variables?.map((v, idx) => (
                  <div key={idx} className="d-flex gap-2 mb-2 align-items-center">
                    <Form.Check
                      checked={v.enabled}
                      onChange={(e) => {
                        const nextVars = [...(editingEnv.variables || [])];
                        nextVars[idx] = { ...nextVars[idx], enabled: e.target.checked };
                        setEditingEnv({ ...editingEnv, variables: nextVars });
                      }}
                    />
                    <Form.Control
                      size="sm"
                      placeholder="变量名"
                      value={v.key}
                      onChange={(e) => {
                        const nextVars = [...(editingEnv.variables || [])];
                        nextVars[idx] = { ...nextVars[idx], key: e.target.value };
                        setEditingEnv({ ...editingEnv, variables: nextVars });
                      }}
                    />
                    <Form.Control
                      size="sm"
                      placeholder="变量值"
                      value={v.value}
                      onChange={(e) => {
                        const nextVars = [...(editingEnv.variables || [])];
                        nextVars[idx] = { ...nextVars[idx], value: e.target.value };
                        setEditingEnv({ ...editingEnv, variables: nextVars });
                      }}
                    />
                    <Button
                      variant="link"
                      className="text-danger p-0"
                      onClick={() => {
                        const nextVars = (editingEnv.variables || []).filter((_, i) => i !== idx);
                        setEditingEnv({ ...editingEnv, variables: nextVars });
                      }}
                    >
                      <FaTimes />
                    </Button>
                  </div>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <div className="d-flex flex-column h-100">
            <div className="text-end mb-3">
              <Button
                variant="success"
                size="sm"
                onClick={() =>
                  setEditingEnv({
                    id: "new",
                    name: "新环境",
                    baseUrl: "",
                    variables: [],
                  })
                }
              >
                <FaPlus className="me-1" />
                新建环境
              </Button>
            </div>

            <ListGroup variant="flush">
              {savedEnvs.length === 0 && (
                <div className="text-center text-muted my-5">暂无环境配置</div>
              )}
              {savedEnvs.map((env) => (
                <ListGroup.Item
                  key={env.id}
                  className="d-flex justify-content-between align-items-center"
                >
                  <div>
                    <div style={{ fontWeight: 600 }}>{env.name}</div>
                    <div className="small text-muted font-monospace">{env.baseUrl}</div>
                    <div className="small text-secondary">
                      {(env.variables || []).length} 个变量
                    </div>
                  </div>
                  <div className="d-flex gap-2">
                    <Button variant="outline-primary" size="sm" onClick={() => setEditingEnv(env)}>
                      <FaEdit />
                    </Button>
                    <Button variant="outline-danger" size="sm" onClick={() => onDeleteEnv(env.id)}>
                      <FaTrash />
                    </Button>
                  </div>
                </ListGroup.Item>
              ))}
            </ListGroup>
          </div>
        )}
      </Modal.Body>
      {editingEnv && (
        <Modal.Footer>
          <Button variant="secondary" onClick={() => setEditingEnv(null)}>
            取消
          </Button>
          <Button variant="primary" onClick={() => onUpdateEnv(editingEnv)}>
            保存环境
          </Button>
        </Modal.Footer>
      )}
    </Modal>
  );
}
