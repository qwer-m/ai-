export function judgeStatusLabel(status: string | null | undefined): string {
  const value = String(status || '').trim().toUpperCase();
  if (value === 'PASS') return '通过 PASS';
  if (value === 'REPAIRABLE') return '可修复 REPAIRABLE';
  if (value === 'REJECT') return '拒绝 REJECT';
  if (value === 'PENDING') return '待确认 PENDING';
  return value || '-';
}

export function shortJudgeStatusLabel(status: string | null | undefined): string {
  const value = String(status || '').trim().toUpperCase();
  if (value === 'PASS') return '通过';
  if (value === 'REPAIRABLE') return '可修复';
  if (value === 'REJECT') return '拒绝';
  if (value === 'PENDING') return '待确认';
  return value || '-';
}

export function coverageTypeLabel(type: string | null | undefined): string {
  const value = String(type || '').trim();
  const normalized = value.toLowerCase();
  if (normalized === 'happy') return '正向路径';
  if (normalized === 'exception') return '异常路径';
  if (normalized === 'boundary') return '边界条件';
  if (normalized === 'risk') return '风险场景';
  if (normalized === 'main' || normalized === 'main_path' || normalized === 'core') return '主流程';
  if (normalized === 'state' || normalized === 'state_transition') return '状态流转';
  return value || '-';
}

export function yesNoLabel(value: unknown): string {
  if (value === true) return '是';
  if (value === false) return '否';
  return '-';
}

export function qualityAssessmentLabel(value: unknown): string {
  const normalized = String(value || '').trim().toLowerCase();
  if (normalized === 'high') return '高';
  if (normalized === 'medium') return '中';
  if (normalized === 'low') return '低';
  if (normalized === 'pass' || normalized === 'passed') return '通过';
  if (normalized === 'fail' || normalized === 'failed') return '未通过';
  return String(value || '-');
}
