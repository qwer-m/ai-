import { useEffect, useMemo, useState } from 'react';
import { OverlayTrigger, Tooltip } from 'react-bootstrap';
import { api } from '../../utils/api';

type Props = {
  provider: string;
  apiKey: string;
  baseUrl: string;
  model: string;
};

type QuotaState = {
  total: number;
  remaining: number;
  supported: boolean;
  loading: boolean;
};

export function QuotaRing({ provider, apiKey, baseUrl, model }: Props) {
  const [quota, setQuota] = useState<QuotaState>({
    total: 0,
    remaining: 0,
    supported: false,
    loading: true,
  });

  useEffect(() => {
    let cancelled = false;

    const fetchData = async () => {
      if (!apiKey && provider !== 'local') {
        if (!cancelled) {
          setQuota({
            total: 0,
            remaining: 0,
            supported: false,
            loading: false,
          });
        }
        return;
      }

      if (!cancelled) {
        setQuota((prev) => ({ ...prev, loading: true }));
      }

      try {
        const res = await api.post<any>('/api/config/quota', {
          provider,
          api_key: apiKey,
          base_url: baseUrl,
          model_name: model,
        });

        if (cancelled) return;

        if (res.supported) {
          setQuota({
            total: parseFloat(res.total),
            remaining: parseFloat(res.remaining),
            supported: true,
            loading: false,
          });
        } else {
          setQuota({
            total: 0,
            remaining: 0,
            supported: false,
            loading: false,
          });
        }
      } catch {
        if (!cancelled) {
          setQuota((prev) => ({ ...prev, loading: false }));
        }
      }
    };

    void fetchData();
    const interval = setInterval(() => {
      void fetchData();
    }, 10000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [provider, apiKey, baseUrl, model]);

  const percent = quota.total > 0 ? (quota.remaining / quota.total) * 100 : 0;
  const stateClass = useMemo(() => {
    if (percent < 20) return 'config-quota-indicator--danger';
    if (percent < 50) return 'config-quota-indicator--warn';
    return 'config-quota-indicator--ok';
  }, [percent]);

  if (!quota.supported) return null;

  return (
    <OverlayTrigger
      placement="top"
      overlay={<Tooltip>余额: {quota.remaining.toFixed(2)} / {quota.total.toFixed(2)}</Tooltip>}
    >
      <div className={`config-quota-indicator ${stateClass}`}>
        <svg viewBox="0 0 36 36">
          <path
            className="config-quota-indicator__track"
            d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
            fill="none"
            strokeWidth="4"
          />
          <path
            className="config-quota-indicator__meter"
            d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
            fill="none"
            strokeWidth="4"
            strokeDasharray={`${percent}, 100`}
          />
        </svg>
      </div>
    </OverlayTrigger>
  );
}
