import { Button, Form, Spinner } from 'react-bootstrap';
import { FaBars, FaCog, FaSave } from 'react-icons/fa';
import type { StandardApiTestingRequestWorkspaceProps } from './StandardApiTestingRequestWorkspace.types';

type Props = Pick<
  StandardApiTestingRequestWorkspaceProps,
  | 'showSidebar'
  | 'setShowSidebar'
  | 'isDragging'
  | 'handleRequestBarMouseDown'
  | 'method'
  | 'setMethod'
  | 'apiPath'
  | 'setApiPath'
  | 'inputRef'
  | 'highlighterRef'
  | 'activeEnvTag'
  | 'showPopup'
  | 'setShowPopup'
  | 'handleInputMouseMove'
  | 'handleInputMouseLeave'
  | 'handlePopupMouseEnter'
  | 'handlePopupMouseLeave'
  | 'getEnvBaseUrlValue'
  | 'setEnvBaseUrlValue'
  | 'handleApiPathBlur'
  | 'loading'
  | 'handleSendRequest'
  | 'handleSaveInterfaceClick'
  | 'handleSaveEnv'
>;

export function StandardApiTestingRequestWorkspaceToolbar({
  showSidebar,
  setShowSidebar,
  isDragging,
  handleRequestBarMouseDown,
  method,
  setMethod,
  apiPath,
  setApiPath,
  inputRef,
  highlighterRef,
  activeEnvTag,
  showPopup,
  setShowPopup,
  handleInputMouseMove,
  handleInputMouseLeave,
  handlePopupMouseEnter,
  handlePopupMouseLeave,
  getEnvBaseUrlValue,
  setEnvBaseUrlValue,
  handleApiPathBlur,
  loading,
  handleSendRequest,
  handleSaveInterfaceClick,
  handleSaveEnv,
}: Props) {
  const methodClass = `api-method-${(method || 'GET').toLowerCase()}`;

  return (
    <>
      <div className="d-flex align-items-center p-2 border-bottom gap-2 flex-shrink-0 standard-api-toolbar">
        <Button
          variant="link"
          className="p-0 text-secondary me-2"
          onClick={() => setShowSidebar(!showSidebar)}
          title={showSidebar ? '收起列表' : '展开列表'}
        >
          <FaBars size={16} />
        </Button>

        <div className="d-flex flex-grow-1 border rounded standard-api-url-bar">
          <Form.Select
            className={`border-0 shadow-none standard-api-method-select ${methodClass}`}
            value={method}
            onChange={(e) => setMethod(e.target.value)}
          >
            <option value="GET">GET</option>
            <option value="POST">POST</option>
            <option value="PUT">PUT</option>
            <option value="DELETE">DELETE</option>
          </Form.Select>

          <div className="d-flex flex-grow-1 align-items-center px-2 border-end position-relative standard-api-path-wrap">
            <div className="position-relative w-100 h-100 d-flex align-items-center">
              <div ref={highlighterRef} className="position-absolute top-0 start-0 w-100 h-100 d-flex align-items-center standard-api-path-highlight">
                {apiPath.split(/(\{\{.*?\}\})/).map((part, index) => {
                  if (part.startsWith('{{') && part.endsWith('}}')) {
                    const isEmpty = part.replace(/[\{\}\s]/g, '').length === 0;
                    const envValue = getEnvBaseUrlValue(part);
                    const isMissingBaseUrl =
                      !isEmpty && (!envValue || !envValue.trim() || envValue === 'null' || envValue === 'undefined');
                    const chipClassName = isEmpty
                      ? 'standard-api-env-chip standard-api-env-chip--empty'
                      : isMissingBaseUrl
                        ? 'standard-api-env-chip standard-api-env-chip--missing'
                        : 'standard-api-env-chip standard-api-env-chip--ok';
                    return (
                      <span key={index} className={chipClassName}>
                        {part}
                      </span>
                    );
                  }

                  return (
                    <span key={index} className="standard-api-path-text">
                      {part}
                    </span>
                  );
                })}
              </div>

              <Form.Control
                ref={inputRef}
                className={`border-0 shadow-none p-0 bg-transparent custom-api-input standard-api-path-input ${apiPath ? 'standard-api-path-input--filled' : ''}`}
                placeholder="Enter request URL"
                value={apiPath}
                onChange={(e) => setApiPath(e.target.value)}
                onBlur={handleApiPathBlur}
                onMouseMove={handleInputMouseMove}
                onMouseLeave={handleInputMouseLeave}
                onScroll={(e) => {
                  if (highlighterRef.current) {
                    highlighterRef.current.scrollLeft = e.currentTarget.scrollLeft;
                  }
                }}
              />
            </div>

            {activeEnvTag && showPopup && (
              <div
                className="position-absolute start-0 end-0 border rounded shadow-sm px-3 py-2 standard-api-env-popup"
                onMouseEnter={handlePopupMouseEnter}
                onMouseLeave={handlePopupMouseLeave}
              >
                <div className="d-flex align-items-center rounded px-2 py-1 border standard-api-env-popup-input-wrap">
                  <span className="small me-2 font-monospace text-primary standard-api-env-label">{activeEnvTag}:</span>
                  <Form.Control
                    size="sm"
                    className="border-0 bg-transparent shadow-none p-0 text-muted"
                    placeholder="Enter Base URL value..."
                    value={getEnvBaseUrlValue(activeEnvTag)}
                    onChange={(e) => setEnvBaseUrlValue(activeEnvTag, e.target.value)}
                    onFocus={() => {
                      setShowPopup(true);
                    }}
                    onBlur={() => {
                      setTimeout(() => setShowPopup(false), 300);
                    }}
                  />
                </div>
              </div>
            )}
          </div>
        </div>

        <Button variant="primary" onClick={handleSendRequest} disabled={loading} className="px-4 text-white standard-api-send-btn">
          {loading ? <Spinner size="sm" animation="border" /> : '发送'}
        </Button>
        <Button
          variant="outline-secondary"
          className="px-3 standard-api-save-btn"
          onClick={handleSaveInterfaceClick}
          title="保存接口"
        >
          <FaSave className="me-2" /> 保存
        </Button>
        <Button variant="light" className="border text-secondary standard-api-env-manage-btn" onClick={handleSaveEnv} title="环境管理">
          <FaCog className="me-2" /> 环境管理
        </Button>
      </div>

      <div
        className={`border-top d-flex align-items-center justify-content-center text-muted flex-shrink-0 standard-api-splitter standard-api-splitter-handle ${isDragging ? 'is-dragging' : ''}`}
        onMouseDown={handleRequestBarMouseDown}
      />
    </>
  );
}
