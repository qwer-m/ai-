import type { BodyMode, FormDataItem, KeyValueItem, RawType } from '../../../standard-api-testing/utils/types';

export const createEmptyKeyValue = (): KeyValueItem => ({ key: '', value: '', desc: '' });

export const createEmptyFormData = (): FormDataItem => ({ key: '', value: '', desc: '', type: 'text' });

export function resolveTargetContentType(
  bodyMode: BodyMode,
  rawType: RawType,
  bodyContent: string,
  formDataParams: FormDataItem[],
  xWwwFormUrlencodedParams: KeyValueItem[],
) {
  let targetType = '';

  if (bodyMode === 'raw') {
    if (rawType === 'JSON') targetType = 'application/json';
    else if (rawType === 'HTML') targetType = 'text/html';
    else if (rawType === 'XML') targetType = 'application/xml';
    else if (rawType === 'JavaScript') targetType = 'application/javascript';
    else if (rawType === 'Text') targetType = 'text/plain';
  } else if (bodyMode === 'x-www-form-urlencoded') {
    targetType = 'application/x-www-form-urlencoded';
  }

  const hasBodyContent =
    (bodyMode === 'raw' && !!bodyContent.trim()) ||
    (bodyMode === 'x-www-form-urlencoded' && xWwwFormUrlencodedParams.some((item) => item.key || item.value)) ||
    (bodyMode === 'form-data' && formDataParams.some((item) => item.key || item.value));

  if (!hasBodyContent) targetType = '';
  if (bodyMode === 'form-data') targetType = '';

  return targetType;
}
