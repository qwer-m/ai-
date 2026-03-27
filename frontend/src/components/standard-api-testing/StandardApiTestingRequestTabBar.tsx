import { Button, Nav } from 'react-bootstrap';
import { FaCookie, FaRobot } from 'react-icons/fa';

type StandardApiTestingRequestTabBarProps = {
  runSubTab: string;
  setRunSubTab: (key: string) => void;
  headerCount: number;
  onOpenCookieManager: () => void;
};

export function StandardApiTestingRequestTabBar({
  runSubTab,
  setRunSubTab,
  headerCount,
  onOpenCookieManager,
}: StandardApiTestingRequestTabBarProps) {
  return (
    <div className="border-bottom px-3 flex-shrink-0 d-flex justify-content-between align-items-end standard-api-tabbar standard-api-tabbar-row">
      <Nav activeKey={runSubTab} onSelect={(k) => setRunSubTab(k || 'params')} className="small custom-nav-tabs">
        <Nav.Item><Nav.Link as="button" type="button" eventKey="params" className="text-secondary" onMouseDown={(e) => e.preventDefault()}>Params</Nav.Link></Nav.Item>
        <Nav.Item><Nav.Link as="button" type="button" eventKey="authorization" className="text-secondary" onMouseDown={(e) => e.preventDefault()}>Authorization</Nav.Link></Nav.Item>
        <Nav.Item><Nav.Link as="button" type="button" eventKey="headers" className="text-secondary" onMouseDown={(e) => e.preventDefault()}>Headers <span className="text-muted ms-1">({headerCount})</span></Nav.Link></Nav.Item>
        <Nav.Item><Nav.Link as="button" type="button" eventKey="body" className="text-secondary" onMouseDown={(e) => e.preventDefault()}>Body</Nav.Link></Nav.Item>
        <Nav.Item><Nav.Link as="button" type="button" eventKey="scripts" className="text-secondary" onMouseDown={(e) => e.preventDefault()}>Scripts</Nav.Link></Nav.Item>
        <Nav.Item><Nav.Link as="button" type="button" eventKey="settings" className="text-secondary" onMouseDown={(e) => e.preventDefault()}>Settings</Nav.Link></Nav.Item>
        <Nav.Item><Nav.Link as="button" type="button" eventKey="ai_prompt" className="text-primary standard-api-tab-ai" onMouseDown={(e) => e.preventDefault()}><FaRobot className="me-1" />AI Gen</Nav.Link></Nav.Item>
      </Nav>
      <Button variant="link" className="text-secondary text-decoration-none pb-2 mb-1 standard-api-cookie-btn" onClick={onOpenCookieManager} size="sm" title="Cookies 管理">
        <FaCookie className="me-1" /> Cookies
      </Button>
    </div>
  );
}
