import { Form } from 'react-bootstrap';
import type { StandardApiTestingRequestWorkspaceProps } from './StandardApiTestingRequestWorkspace.types';

type Props = Pick<
  StandardApiTestingRequestWorkspaceProps,
  'activeScriptTab' | 'setActiveScriptTab' | 'preRequestScript' | 'setPreRequestScript' | 'postResponseScript' | 'setPostResponseScript'
>;

export function StandardApiTestingRequestWorkspaceScriptsTab({
  activeScriptTab,
  setActiveScriptTab,
  preRequestScript,
  setPreRequestScript,
  postResponseScript,
  setPostResponseScript,
}: Props) {
  return (
    <div className="custom-scrollbar position-absolute top-0 start-0 w-100 h-100 standard-api-scroll-pane standard-api-pane-scripts">
      <div className="d-flex h-100">
        <div className="border-end p-2 standard-api-side-selector">
          <div className="d-flex flex-column gap-1">
            <div
              className={`px-3 py-2 small rounded standard-api-side-selector-item ${activeScriptTab === 'pre' ? 'is-active' : ''}`}
              onClick={() => setActiveScriptTab('pre')}
            >
              Pre-request (前置脚本)
            </div>
            <div
              className={`px-3 py-2 small rounded standard-api-side-selector-item ${activeScriptTab === 'post' ? 'is-active' : ''}`}
              onClick={() => setActiveScriptTab('post')}
            >
              Post-response (后置脚本)
            </div>
          </div>
        </div>
        <div className="flex-grow-1 p-0 d-flex flex-column">
          <Form.Control
            as="textarea"
            className="flex-grow-1 border-0 p-3 font-monospace small bg-transparent standard-api-raw-textarea"
            placeholder={activeScriptTab === 'pre' ? '// 在此编写前置脚本 (Pre-request scripts)...' : '// 在此编写后置脚本 (Post-response scripts)...'}
            value={activeScriptTab === 'pre' ? preRequestScript : postResponseScript}
            onChange={(e) => {
              if (activeScriptTab === 'pre') {
                setPreRequestScript(e.target.value);
              } else {
                setPostResponseScript(e.target.value);
              }
            }}
            spellCheck={false}
          />
        </div>
      </div>
    </div>
  );
}
