import type { GenDiagEvent } from './diagParser';

// 中文注释：调试面板本地预览用 mock 事件序列。
export const mockGenDiagEvents: GenDiagEvent[] = [
  {
    kind: 'generation_mode',
    mode: 'biz_key_multi_pass',
    biz_keys: ['org_close_rule', 'org_open_rule'],
    current_biz_key: 'org_close_rule',
  },
  { kind: 'biz_key_pass_stage', biz_key: 'org_close_rule', stage: 'primary', case_count: 6 },
  { kind: 'biz_key_pass_stage', biz_key: 'org_close_rule', stage: 'gap', case_count: 9 },
  { kind: 'biz_key_pass_stage', biz_key: 'org_close_rule', stage: 'review', case_count: 8 },
  { kind: 'biz_key_pass_stage', biz_key: 'org_open_rule', stage: 'primary', case_count: 4 },
  { kind: 'biz_key_pass_stage', biz_key: 'org_open_rule', stage: 'review', case_count: 4 },
  {
    kind: 'coverage_check',
    data: {
      total_rules: 3,
      covered_rules: 1,
      missing_rules: 2,
      rule_diagnostics: [
        {
          rule_id: 'REQ-023',
          covered: true,
          coverage_types: ['happy', 'boundary'],
          missing_types: [],
          biz_key: 'org_close_rule',
          rule_text: '关闭机构后，下家不可继续发起充值。',
        },
        {
          rule_id: 'REQ-024',
          covered: false,
          coverage_types: [],
          missing_types: ['exception'],
          biz_key: 'org_close_rule',
          rule_text: '异常场景需返回明确错误码。',
        },
        {
          rule_id: 'REQ-025',
          covered: false,
          coverage_types: [],
          missing_types: ['happy', 'boundary'],
          biz_key: 'org_open_rule',
          rule_text: '开店规则需覆盖正常与边界值。',
        },
      ],
    },
  },
];

