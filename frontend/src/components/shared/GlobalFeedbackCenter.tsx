import { useEffect, useMemo, useState } from 'react';
import { Toast, ToastContainer } from 'react-bootstrap';
import { APP_FEEDBACK_EVENT, type FeedbackLevel, type FeedbackPayload } from '../../utils/feedback';

type FeedbackItem = {
  id: number;
  title: string;
  message: string;
  level: FeedbackLevel;
};

const LEVEL_TO_BOOTSTRAP: Record<FeedbackLevel, string> = {
  success: 'success',
  error: 'danger',
  warning: 'warning',
  info: 'primary',
};

export function GlobalFeedbackCenter() {
  const [items, setItems] = useState<FeedbackItem[]>([]);

  useEffect(() => {
    const onFeedback = (evt: Event) => {
      const custom = evt as CustomEvent<FeedbackPayload>;
      const detail = custom.detail;
      if (!detail?.message) return;
      const next: FeedbackItem = {
        id: Date.now() + Math.floor(Math.random() * 1000),
        title: detail.title || (detail.level === 'error' ? '错误' : '提示'),
        message: detail.message,
        level: detail.level || 'info',
      };
      setItems((prev) => [...prev.slice(-2), next]);
    };
    window.addEventListener(APP_FEEDBACK_EVENT, onFeedback);
    return () => {
      window.removeEventListener(APP_FEEDBACK_EVENT, onFeedback);
    };
  }, []);

  const visibleItems = useMemo(() => items.slice(-3), [items]);

  return (
    <ToastContainer position="top-end" className="p-3 global-feedback-center">
      {visibleItems.map((item) => (
        <Toast
          key={item.id}
          show
          onClose={() => setItems((prev) => prev.filter((x) => x.id !== item.id))}
          delay={3400}
          autohide
          bg={LEVEL_TO_BOOTSTRAP[item.level]}
        >
          <Toast.Header closeButton>
            <strong className="me-auto">{item.title}</strong>
          </Toast.Header>
          <Toast.Body className="text-white">{item.message}</Toast.Body>
        </Toast>
      ))}
    </ToastContainer>
  );
}

