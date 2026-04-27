import type { ResponsePanelProps } from './ResponsePanel.types';
import { KeyValueTable } from './ResponsePanel.utils';

type Props = Pick<ResponsePanelProps, 'responseHeaders' | 'sentHeaders'>;

export function ResponsePanelHeadersTab({ responseHeaders, sentHeaders }: Props) {
  return (
    <div className="flex-grow-1 overflow-auto p-3 standard-api-panel-scroll">
      <h6 className="text-secondary border-bottom pb-2 mb-3">响应头 (Response Headers)</h6>
      <KeyValueTable emptyText="无响应头" columns={['键 (Key)', '值 (Value)']} entries={Object.entries(responseHeaders)} />

      <h6 className="text-secondary border-bottom pb-2 mb-3 mt-4">请求头 (Request Headers - 已发送)</h6>
      <KeyValueTable emptyText="无请求头" columns={['键 (Key)', '值 (Value)']} entries={Object.entries(sentHeaders)} />
    </div>
  );
}
