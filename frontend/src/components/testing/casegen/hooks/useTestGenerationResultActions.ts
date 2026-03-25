import { getAuthHeaders } from '../../../../utils/api';
import type { TestGenerationMode } from '../../../test-generation/types';
import { getCopyPayload, parseStreamingArrayContent, translateError } from './testGenerationCaseUtils';

type Args = {
  mode: TestGenerationMode;
  file: File | null;
  result: any;
  streamingContent: string;
  savedFileName: string;
  setTextResult: (value: any) => void;
  setFileResult: (value: any) => void;
  setTextStreamingContent: (value: string) => void;
  setFileStreamingContent: (value: string) => void;
  onLog: (msg: string) => void;
  setToastType: (type: 'success' | 'error') => void;
  setToastMsg: (msg: string | null) => void;
};

export function useTestGenerationResultActions({
  mode,
  file,
  result,
  streamingContent,
  savedFileName,
  setTextResult,
  setFileResult,
  setTextStreamingContent,
  setFileStreamingContent,
  onLog,
  setToastType,
  setToastMsg,
}: Args) {
  const handleExportExcel = async () => {
    let exportData: any[] = [];
    if (Array.isArray(result) && result.length > 0) exportData = [...result];
    else if (streamingContent) {
      const parsed = parseStreamingArrayContent(streamingContent);
      exportData = parsed.length > 0 ? parsed : [{ raw_content: streamingContent }];
    }
    if (!exportData.length) return;
    try {
      const resp = await fetch('/api/export-tests-excel', { method: 'POST', headers: { 'Content-Type': 'application/json', ...getAuthHeaders() }, body: JSON.stringify(exportData) });
      if (!resp.ok) throw new Error('Export failed');
      const blob = await resp.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      let filename = 'test_cases.xlsx';
      if (mode === 'file') {
        const nameToUse = file ? file.name : savedFileName;
        if (nameToUse) {
          const name = nameToUse.substring(0, nameToUse.lastIndexOf('.')) || nameToUse;
          filename = `${name}_娴嬭瘯鐢ㄤ緥.xlsx`;
        }
      }
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      onLog('瀵煎嚭 Excel 鎴愬姛');
    } catch (e) {
      const msg = await translateError(e);
      onLog(`瀵煎嚭澶辫触: ${msg}`);
    }
  };

  const handleClearCurrent = () => {
    if (mode === 'text') {
      setTextResult(null);
      setTextStreamingContent('');
    } else {
      setFileResult(null);
      setFileStreamingContent('');
    }
    onLog('Cleared generated result');
  };

  const handleCopyCurrent = () => {
    const content = getCopyPayload(result, streamingContent);
    if (!content) return;
    navigator.clipboard.writeText(content)
      .then(() => { setToastType('success'); setToastMsg('Copied to clipboard'); })
      .catch(() => { setToastType('error'); setToastMsg('澶嶅埗澶辫触锛岃鎵嬪姩澶嶅埗'); });
  };

  return {
    handleExportExcel,
    handleClearCurrent,
    handleCopyCurrent,
  };
}
