import React from 'react';
import { Card } from 'react-bootstrap';
import { FaImage } from 'react-icons/fa';

interface LivePreviewProps {
    executionId: number | null;
    status: string;
    logs: string;
    screenshotPaths: string[];
    isPolling: boolean;
}

export const LivePreview: React.FC<LivePreviewProps> = ({ executionId, status, screenshotPaths, isPolling }) => {
    const latestScreenshot = screenshotPaths.length > 0 ? screenshotPaths[screenshotPaths.length - 1] : null;

    return (
        <div className="ui-automation-live-preview h-100 d-flex flex-column gap-3">
            <div className="flex-grow-1 d-flex gap-3 ui-automation-min-h-0">
                <Card className="ui-automation-live-card border-0 w-100 d-flex flex-column overflow-hidden">
                    <Card.Header className="ui-automation-live-card-head py-2 small fw-bold border-bottom d-flex align-items-center">
                        <FaImage className="me-2 text-primary" /> 实时画面
                        <span className="ms-2 text-muted fw-normal">{isPolling ? '轮询中' : status}</span>
                    </Card.Header>
                    <Card.Body className="ui-automation-live-card-body p-0 d-flex align-items-center justify-content-center position-relative overflow-hidden">
                        {latestScreenshot && executionId ? (
                            <img
                                src={`/api/screenshots/${executionId}/${latestScreenshot.split(/[/\\]/).pop()}`}
                                alt="Live Preview"
                                className="ui-automation-live-image"
                            />
                        ) : (
                            <div className="text-center text-muted">
                                <FaImage size={48} className="mb-2 opacity-25" />
                                <p className="small mb-0">暂无截图</p>
                            </div>
                        )}
                    </Card.Body>
                </Card>
            </div>
        </div>
    );
};
