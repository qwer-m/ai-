import { Button, Modal } from "react-bootstrap";
import { FaCookie, FaPlus, FaTrash } from "react-icons/fa";
import type { Dispatch, SetStateAction } from "react";

type CookieManagerModalProps = {
  show: boolean;
  onHide: () => void;
  cookieJar: Record<string, string>;
  setCookieJar: Dispatch<SetStateAction<Record<string, string>>>;
};

export function CookieManagerModal({
  show,
  onHide,
  cookieJar,
  setCookieJar,
}: CookieManagerModalProps) {
  // CookieJar 作为请求上下文的一部分，支持手动补录、逐条删除和一键清空。
  return (
    <Modal show={show} onHide={onHide} size="lg" centered>
      <Modal.Header closeButton>
        <Modal.Title>Cookies 管理</Modal.Title>
      </Modal.Header>
      <Modal.Body style={{ minHeight: "300px", maxHeight: "600px", overflowY: "auto" }}>
        <div className="alert alert-info small mb-3">
          <FaCookie className="me-2" />
          Cookies 由系统自动管理，会从响应中自动捕获，并在后续请求中自动发送。
        </div>

        <div className="d-flex justify-content-between align-items-center mb-3">
          <h6 className="mb-0">已保存 Cookies（{Object.keys(cookieJar).length}）</h6>
          <div className="d-flex gap-2">
            <Button size="sm" variant="outline-danger" onClick={() => setCookieJar({})}>
              清空全部
            </Button>
            <Button
              size="sm"
              variant="primary"
              onClick={() => {
                const key = prompt("Cookie 名称：");
                if (!key) return;
                const value = prompt("Cookie 值：");
                if (value !== null && value !== "") {
                  setCookieJar((prev) => ({ ...prev, [key]: value }));
                }
              }}
            >
              <FaPlus className="me-1" /> 添加 Cookie
            </Button>
          </div>
        </div>

        {Object.keys(cookieJar).length > 0 ? (
          <table className="table table-hover table-sm">
            <thead className="bg-light">
              <tr>
                <th>名称</th>
                <th>值</th>
                <th style={{ width: "60px" }}></th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(cookieJar).map(([k, v]) => (
                <tr key={k}>
                  <td className="text-secondary" style={{ fontWeight: 600 }}>
                    {k}
                  </td>
                  <td className="font-monospace text-break small">{v}</td>
                  <td>
                    <Button
                      variant="link"
                      className="p-0 text-danger"
                      onClick={() => {
                        const newJar = { ...cookieJar };
                        delete newJar[k];
                        setCookieJar(newJar);
                      }}
                    >
                      <FaTrash size={12} />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="text-center text-muted py-5">暂无 Cookie</div>
        )}
      </Modal.Body>
      <Modal.Footer>
        <Button variant="secondary" onClick={onHide}>
          关闭
        </Button>
      </Modal.Footer>
    </Modal>
  );
}
