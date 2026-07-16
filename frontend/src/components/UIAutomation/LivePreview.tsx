import React, { useEffect, useRef, useState } from 'react';
import { Card, Spinner } from 'react-bootstrap';
import { FaExclamationTriangle, FaImage } from 'react-icons/fa';
import { api } from '../../utils/api';

interface LivePreviewProps {
    executionId: number | null;
    status: string;
    logs: string;
    screenshotPaths: string[];
    isPolling: boolean;
    automationType: 'web' | 'app';
}

export const LivePreview: React.FC<LivePreviewProps> = ({
    executionId,
    status,
    screenshotPaths,
    isPolling,
    automationType,
}) => {
    const latestScreenshot = screenshotPaths.length > 0 ? screenshotPaths[screenshotPaths.length - 1] : null;
    const [previewUrl, setPreviewUrl] = useState('');
    const [previewError, setPreviewError] = useState('');
    const [frameLoading, setFrameLoading] = useState(false);
    const objectUrlRef = useRef('');
    const useDeviceFeed = automationType === 'app'
        && (!latestScreenshot || status === 'running' || status === 'pending' || status === 'idle');

    useEffect(() => {
        let disposed = false;
        let timer: ReturnType<typeof setTimeout> | undefined;

        const replaceFrame = (blob: Blob) => {
            const nextUrl = URL.createObjectURL(blob);
            if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
            objectUrlRef.current = nextUrl;
            setPreviewUrl(nextUrl);
        };

        const loadFrame = async () => {
            const screenshotUrl = latestScreenshot && executionId
                ? `/api/ui-automation/screenshots/${executionId}/${latestScreenshot.split(/[/\\]/).pop()}`
                : '';
            const requestUrl = useDeviceFeed ? '/api/ui-automation/device-screenshot' : screenshotUrl;
            if (!requestUrl) {
                if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
                objectUrlRef.current = '';
                setPreviewUrl('');
                setPreviewError('');
                return;
            }
            setFrameLoading(!objectUrlRef.current);
            try {
                const blob = await api.getBlob(requestUrl);
                if (!disposed) {
                    replaceFrame(blob);
                    setPreviewError('');
                }
            } catch (error) {
                if (!disposed) setPreviewError(error instanceof Error ? error.message : String(error));
            } finally {
                if (!disposed) {
                    setFrameLoading(false);
                    if (useDeviceFeed) timer = setTimeout(() => void loadFrame(), 1200);
                }
            }
        };

        void loadFrame();
        return () => {
            disposed = true;
            if (timer) clearTimeout(timer);
        };
    }, [automationType, executionId, latestScreenshot, status, useDeviceFeed]);

    useEffect(() => () => {
        if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
    }, []);

    return (
        <div className="ui-automation-live-preview h-100 d-flex flex-column gap-3">
            <div className="flex-grow-1 d-flex gap-3 ui-automation-min-h-0">
                <Card className="ui-automation-live-card border-0 w-100 d-flex flex-column overflow-hidden">
                    <Card.Header className="ui-automation-live-card-head py-2 small fw-bold d-flex align-items-center">
                        <FaImage className="me-2 text-primary" /> 实时画面
                        <span className="ms-2 text-muted fw-normal">
                            {useDeviceFeed ? '设备实时画面' : isPolling ? '轮询中' : status}
                        </span>
                    </Card.Header>
                    <Card.Body className="ui-automation-live-card-body p-0 d-flex align-items-center justify-content-center position-relative overflow-hidden">
                        {previewUrl ? (
                            <img src={previewUrl} alt="AI 执行实时画面" className="ui-automation-live-image" />
                        ) : frameLoading ? (
                            <div className="text-center text-muted">
                                <Spinner animation="border" size="sm" className="mb-2" />
                                <p className="small mb-0">正在读取设备画面</p>
                            </div>
                        ) : (
                            <div className="text-center text-muted">
                                <FaImage size={48} className="mb-2 opacity-25" />
                                <p className="small mb-0">暂无截图</p>
                            </div>
                        )}
                        {previewError ? (
                            <div className="ui-automation-live-error">
                                <FaExclamationTriangle className="me-2" />{previewError}
                            </div>
                        ) : null}
                    </Card.Body>
                </Card>
            </div>
        </div>
    );
};
