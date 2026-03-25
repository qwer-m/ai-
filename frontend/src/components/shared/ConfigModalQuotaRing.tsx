import { useEffect, useState } from 'react';
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

  const fetchData = async () => {
    if (!apiKey && provider !== 'local') return;

    try {
      const res = await api.post<any>('/api/config/quota', {
        provider,
        api_key: apiKey,
        base_url: baseUrl,
        model_name: model,
      });

      if (res.supported) {
        setQuota({
          total: parseFloat(res.total),
          remaining: parseFloat(res.remaining),
          supported: true,
          loading: false,
        });
      } else {
        setQuota((prev) => ({ ...prev, supported: false, loading: false }));
      }
    } catch {
      setQuota((prev) => ({ ...prev, loading: false }));
    }
  };

  useEffect(() => {
    void fetchData();
    const interval = setInterval(() => {
      void fetchData();
    }, 10000);
    return () => clearInterval(interval);
  }, [provider, apiKey, baseUrl]);

  if (!quota.supported) return null;

  const percent = quota.total > 0 ? (quota.remaining / quota.total) * 100 : 0;
  const color = percent < 20 ? '#dc3545' : percent < 50 ? '#ffc107' : '#28a745';

  return (
    <OverlayTrigger
      placement="top"
      overlay={<Tooltip>浣欓: ${quota.remaining.toFixed(2)} / ${quota.total.toFixed(2)}</Tooltip>}
    >
      <div
        style={{
          position: 'absolute',
          right: '10px',
          top: '50%',
          transform: 'translateY(-50%)',
          width: '20px',
          height: '20px',
          cursor: 'help',
          zIndex: 5,
        }}
      >
        <svg viewBox="0 0 36 36">
          <path
            d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
            fill="none"
            stroke="#eee"
            strokeWidth="4"
          />
          <path
            d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
            fill="none"
            stroke={color}
            strokeWidth="4"
            strokeDasharray={`${percent}, 100`}
          />
        </svg>
      </div>
    </OverlayTrigger>
  );
}
