import { Form } from 'react-bootstrap';
import { FaFileAlt } from 'react-icons/fa';

export type CaseConversionStatus = 'idle' | 'converting' | 'converted' | 'failed';

export interface ImportedUITestCase {
  key: string;
  source_index: number;
  id: string;
  description: string;
  test_module: string;
  preconditions: string[];
  steps: string[];
  test_input: string;
  expected_result: string;
  priority: string;
  conversion_status?: CaseConversionStatus;
  conversion_message?: string;
}

interface Props {
  filename: string;
  cases: ImportedUITestCase[];
  selectedKeys: Set<string>;
  onToggle: (key: string) => void;
  onToggleAll: () => void;
}

const conversionText: Record<CaseConversionStatus, string> = {
  idle: '待转化',
  converting: 'AI 理解中',
  converted: '已转为脚本',
  failed: '转化失败',
};

export function ImportedTestCasesView({ filename, cases, selectedKeys, onToggle, onToggleAll }: Props) {
  const allSelected = cases.length > 0 && selectedKeys.size === cases.length;

  return (
    <div className="ui-imported-cases h-100 d-flex flex-column">
      <div className="ui-imported-cases-head border-bottom px-3 py-2 d-flex align-items-center gap-3">
        <div className="min-w-0">
          <div className="small fw-bold"><FaFileAlt className="me-2 text-primary" />已上传测试用例</div>
          <div className="small text-muted text-truncate mt-1">{filename} · {cases.length} 条</div>
        </div>
      </div>
      <div className="ui-imported-case-table-wrap flex-grow-1 overflow-auto">
        <table className="ui-imported-case-table w-100">
          <thead>
            <tr>
              <th className="ui-imported-case-check">
                <Form.Check
                  aria-label="选择全部测试用例"
                  checked={allSelected}
                  onChange={onToggleAll}
                />
              </th>
              <th>用例</th>
              <th>执行步骤</th>
              <th>预期结果</th>
              <th>转化状态</th>
            </tr>
          </thead>
          <tbody>
            {cases.map((testCase) => {
              const conversionStatus = testCase.conversion_status || 'idle';
              return (
                <tr key={testCase.key} className={selectedKeys.has(testCase.key) ? 'is-selected' : ''}>
                  <td className="ui-imported-case-check">
                    <Form.Check
                      aria-label={`选择测试用例 ${testCase.id}`}
                      checked={selectedKeys.has(testCase.key)}
                      onChange={() => onToggle(testCase.key)}
                    />
                  </td>
                  <td>
                    <div className="d-flex align-items-center gap-2 mb-1">
                      <span className="ui-case-id">{testCase.id}</span>
                      {testCase.priority ? <span className="ui-case-priority">{testCase.priority}</span> : null}
                    </div>
                    <div className="fw-semibold">{testCase.description}</div>
                    {testCase.test_module ? <div className="small text-muted mt-1">{testCase.test_module}</div> : null}
                  </td>
                  <td>
                    {testCase.steps.length > 0 ? (
                      <ol className="ui-case-step-preview mb-0">
                        {testCase.steps.map((step, index) => <li key={`${testCase.key}-${index}`}>{step}</li>)}
                      </ol>
                    ) : <span className="text-muted">未填写</span>}
                  </td>
                  <td>{testCase.expected_result || <span className="text-muted">未填写</span>}</td>
                  <td>
                    <span className={`ui-case-conversion is-${conversionStatus}`}>{conversionText[conversionStatus]}</span>
                    {testCase.conversion_message ? <div className="small text-muted mt-1 ui-case-conversion-message">{testCase.conversion_message}</div> : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
