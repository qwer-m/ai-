export type FeedbackLevel = 'success' | 'error' | 'warning' | 'info';

export type FeedbackPayload = {
  message: string;
  level?: FeedbackLevel;
  title?: string;
};

export const APP_FEEDBACK_EVENT = 'app:feedback';

/**
 * 全局反馈事件派发器：
 * 让跨模块提示统一走 toast，而不是混用 alert / 局部提示。
 */
export function emitFeedback(payload: FeedbackPayload) {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent<FeedbackPayload>(APP_FEEDBACK_EVENT, { detail: payload }));
}

/**
 * 把浏览器 alert 软桥接到全局 toast，避免阻塞式弹窗打断操作流。
 */
export function installAlertBridge() {
  if (typeof window === 'undefined') {
    return () => undefined;
  }
  const originAlert = window.alert.bind(window);
  window.alert = (message?: unknown) => {
    const text = typeof message === 'string' ? message : String(message ?? '');
    emitFeedback({
      title: '系统提示',
      message: text || '操作提示',
      level: 'warning',
    });
  };
  return () => {
    window.alert = originAlert;
  };
}

