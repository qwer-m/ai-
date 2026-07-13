import type { Dispatch, MutableRefObject, SetStateAction } from 'react';
import { Button, Dropdown, Form, Nav } from 'react-bootstrap';
import { FaChevronDown, FaFile } from 'react-icons/fa';
import { highlightJson } from './utils/jsonHighlight';
import { FormDataEditor, KvEditor } from './RequestEditors';
import type { BodyMode, FormDataItem, KeyValueItem, RawType } from './utils/types';
import { parseBulkText, stringifyBulkItems } from './RequestEditors';

export type StandardApiTestingRequestBodyTabProps = {
  bodyMode: BodyMode;
  setBodyMode: Dispatch<SetStateAction<BodyMode>>;
  rawType: RawType;
  setRawType: Dispatch<SetStateAction<RawType>>;
  bodyContent: string;
  setBodyContent: Dispatch<SetStateAction<string>>;
  formDataParams: FormDataItem[];
  setFormDataParams: Dispatch<SetStateAction<FormDataItem[]>>;
  xWwwFormUrlencodedParams: KeyValueItem[];
  setXWwwFormUrlencodedParams: Dispatch<SetStateAction<KeyValueItem[]>>;
  binaryFile: { name: string; data: string } | null;
  setBinaryFile: Dispatch<SetStateAction<{ name: string; data: string } | null>>;
  graphqlQuery: string;
  setGraphqlQuery: Dispatch<SetStateAction<string>>;
  graphqlVariables: string;
  setGraphqlVariables: Dispatch<SetStateAction<string>>;
  isBulkEditFormData: boolean;
  setIsBulkEditFormData: Dispatch<SetStateAction<boolean>>;
  formDataBulkText: string;
  setFormDataBulkText: Dispatch<SetStateAction<string>>;
  isBulkEditBody: boolean;
  setIsBulkEditBody: Dispatch<SetStateAction<boolean>>;
  bodyBulkText: string;
  setBodyBulkText: Dispatch<SetStateAction<string>>;
  bodyHighlighterRef: MutableRefObject<HTMLDivElement | null>;
  handleBodyScroll: (e: React.UIEvent<HTMLTextAreaElement>) => void;
};

export function StandardApiTestingRequestBodyTab({
  bodyMode,
  setBodyMode,
  rawType,
  setRawType,
  bodyContent,
  setBodyContent,
  formDataParams,
  setFormDataParams,
  xWwwFormUrlencodedParams,
  setXWwwFormUrlencodedParams,
  binaryFile,
  setBinaryFile,
  graphqlQuery,
  setGraphqlQuery,
  graphqlVariables,
  setGraphqlVariables,
  isBulkEditFormData,
  setIsBulkEditFormData,
  formDataBulkText,
  setFormDataBulkText,
  isBulkEditBody,
  setIsBulkEditBody,
  bodyBulkText,
  setBodyBulkText,
  bodyHighlighterRef,
  handleBodyScroll,
}: StandardApiTestingRequestBodyTabProps) {
  const toggleFormDataBulk = () => {
    if (!isBulkEditFormData) {
      setFormDataBulkText(stringifyBulkItems(formDataParams));
    } else {
      setFormDataParams(
        parseBulkText(formDataBulkText).map((item) => ({
          ...item,
          type: 'text' as const,
          src: '',
        })),
      );
    }
    setIsBulkEditFormData(!isBulkEditFormData);
  };

  const toggleUrlEncodedBulk = () => {
    if (!isBulkEditBody) {
      setBodyBulkText(stringifyBulkItems(xWwwFormUrlencodedParams));
    } else {
      setXWwwFormUrlencodedParams(parseBulkText(bodyBulkText));
    }
    setIsBulkEditBody(!isBulkEditBody);
  };

  const handleFormDataBulkChange = (val: string) => {
    setFormDataBulkText(val);
    const parsed = parseBulkText(val).map((item) => ({
      ...item,
      type: 'text' as const,
      src: '',
    }));
    setFormDataParams(parsed.length > 0 ? parsed : [{ key: '', value: '', desc: '', type: 'text', src: '' }]);
  };

  const hiddenBody = bodyMode === 'none';

  return (
    <div className={`custom-scrollbar position-absolute top-0 start-0 w-100 h-100 standard-api-scroll-pane standard-api-body-pane ${hiddenBody ? 'is-hidden' : ''}`}>
      <div className="w-100 d-flex flex-column standard-api-body-root">
        <div className="d-flex flex-wrap gap-3 px-3 py-2 small border-bottom bg-light standard-api-body-modebar">
          <Form.Check type="radio" label="none" checked={bodyMode === 'none'} onChange={() => setBodyMode('none')} inline id="body-none" className="mb-0" />
          <Form.Check type="radio" label="form-data" checked={bodyMode === 'form-data'} onChange={() => setBodyMode('form-data')} inline id="body-form" className="mb-0" />
          <Form.Check type="radio" label="x-www-form-urlencoded" checked={bodyMode === 'x-www-form-urlencoded'} onChange={() => setBodyMode('x-www-form-urlencoded')} inline id="body-url" className="mb-0" />
          <Form.Check type="radio" label="raw" checked={bodyMode === 'raw'} onChange={() => setBodyMode('raw')} inline id="body-raw" className="mb-0" />
          <Form.Check type="radio" label="binary" checked={bodyMode === 'binary'} onChange={() => setBodyMode('binary')} inline id="body-binary" className="mb-0" />
          <Form.Check type="radio" label="GraphQL" checked={bodyMode === 'graphql'} onChange={() => setBodyMode('graphql')} inline id="body-graphql" className="mb-0" />

          {bodyMode === 'raw' && (
            <Nav className="ms-auto">
              <Dropdown>
                <Dropdown.Toggle variant="link" size="sm" className="text-decoration-none p-0 text-primary small">
                  {rawType} <FaChevronDown size={8} />
                </Dropdown.Toggle>
                <Dropdown.Menu align="end">
                  {['Text', 'JavaScript', 'JSON', 'HTML', 'XML'].map((t) => (
                    <Dropdown.Item key={t} onClick={() => setRawType(t as RawType)} active={rawType === t}>
                      {t}
                    </Dropdown.Item>
                  ))}
                </Dropdown.Menu>
              </Dropdown>
            </Nav>
          )}
        </div>

        {bodyMode !== 'none' ? (
          <div className="position-relative d-flex flex-column h-100">
            {bodyMode === 'raw' && rawType === 'JSON' && (
              <div
                ref={bodyHighlighterRef}
                className="position-absolute top-0 start-0 w-100 h-100 font-monospace small p-3 standard-api-body-highlighter"
                dangerouslySetInnerHTML={highlightJson(bodyContent)}
              />
            )}

            {bodyMode === 'raw' ? (
              <Form.Control
                as="textarea"
                className={`w-100 font-monospace small border-0 p-3 bg-transparent flex-grow-1 standard-api-body-raw-input ${bodyMode === 'raw' && rawType === 'JSON' ? 'is-json' : ''}`}
                value={bodyContent}
                onChange={(e) => {
                  setBodyContent(e.target.value);
                  e.target.style.height = 'auto';
                  e.target.style.height = `${e.target.scrollHeight}px`;
                }}
                onScroll={handleBodyScroll}
                placeholder={bodyMode === 'raw' && rawType === 'JSON' ? '{\n  "key": "value"\n}' : '请求体内容...'}
                spellCheck={false}
              />
            ) : bodyMode === 'form-data' ? (
              <FormDataEditor
                items={formDataParams}
                onChange={setFormDataParams}
                isBulk={isBulkEditFormData}
                onToggleBulk={toggleFormDataBulk}
                bulkText={formDataBulkText}
                onBulkChange={handleFormDataBulkChange}
              />
            ) : bodyMode === 'x-www-form-urlencoded' ? (
              <KvEditor
                items={xWwwFormUrlencodedParams}
                onChange={setXWwwFormUrlencodedParams}
                isBulk={isBulkEditBody}
                onToggleBulk={toggleUrlEncodedBulk}
                bulkText={bodyBulkText}
                onBulkChange={setBodyBulkText}
              />
            ) : bodyMode === 'binary' ? (
              <div className="p-4 d-flex flex-column align-items-center justify-content-center h-100 text-secondary standard-api-binary-pane">
                <div className="mb-3">
                  <FaFile className="me-2" size={24} />
                  <span>{binaryFile ? binaryFile.name : '选择要上传的文件'}</span>
                </div>
                <div className="position-relative">
                  <Button variant="outline-primary" size="sm" onClick={() => document.getElementById('binary-file-input')?.click()}>
                    {binaryFile ? '替换文件' : '选择文件'}
                  </Button>
                  <Form.Control
                    id="binary-file-input"
                    type="file"
                    className="d-none"
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) => {
                      const file = e.target.files?.[0];
                      if (!file) return;
                      const reader = new FileReader();
                      reader.onload = (ev) => {
                        const res = ev.target?.result as string;
                        setBinaryFile({ name: file.name, data: res });
                      };
                      reader.readAsDataURL(file);
                    }}
                  />
                </div>
                {binaryFile && (
                  <div className="small text-muted mt-2">
                    已选择: {binaryFile.name}
                    <Button variant="link" className="text-danger p-0 ms-2 small text-decoration-none" onClick={() => setBinaryFile(null)}>
                      清除
                    </Button>
                  </div>
                )}
                <div className="small text-muted mt-2">文件内容将作为请求体发送 (Base64)</div>
              </div>
            ) : bodyMode === 'graphql' ? (
              <div className="d-flex h-100 w-100">
                <div className="w-50 h-100 d-flex flex-column border-end">
                  <div className="px-3 py-1 bg-light border-bottom small fw-bold text-secondary">查询 (QUERY)</div>
                  <Form.Control
                    as="textarea"
                    className="flex-grow-1 border-0 p-3 font-monospace small bg-transparent standard-api-raw-textarea"
                    value={graphqlQuery}
                    onChange={(e) => setGraphqlQuery(e.target.value)}
                    placeholder="query { ... }"
                    spellCheck={false}
                  />
                </div>
                <div className="w-50 h-100 d-flex flex-column">
                  <div className="px-3 py-1 bg-light border-bottom small fw-bold text-secondary">变量 (GRAPHQL VARIABLES)</div>
                  <Form.Control
                    as="textarea"
                    className="flex-grow-1 border-0 p-3 font-monospace small bg-transparent standard-api-raw-textarea"
                    value={graphqlVariables}
                    onChange={(e) => setGraphqlVariables(e.target.value)}
                    placeholder="{ ... }"
                    spellCheck={false}
                  />
                </div>
              </div>
            ) : (
              <div className="p-3 text-muted small">不支持的 Body 类型: {bodyMode}</div>
            )}
          </div>
        ) : (
          <div className="d-flex align-items-center justify-content-center flex-grow-1 text-muted small standard-api-body-empty">
            This request has no body.
          </div>
        )}
      </div>
    </div>
  );
}
