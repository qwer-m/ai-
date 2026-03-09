import React, { useRef, useEffect } from 'react';
import { Card } from 'react-bootstrap';
import { FaImage } from 'react-icons/fa';

interface LivePreviewProps {
    executionId: number | null;
    status: string;
    logs: string;
    screenshotPaths: string[];
    isPolling: boolean;
}

export const LivePreview: React.FC<LivePreviewProps> = ({ 
    executionId, 
    status, 
    logs, 
    screenshotPaths,
    isPolling
}) => {
    const logEndRef = useRef<HTMLDivElement>(null);
    const latestScreenshot = screenshotPaths.length > 0 ? screenshotPaths[screenshotPaths.length - 1] : null;

    useEffect(() => {
        logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [logs]);

    return (
        <div className="h-100 d-flex flex-column gap-3">
            <div className="flex-grow-1 d-flex gap-3" style={{minHeight: 0}}>
                {/* Left: Screenshot */}
                <Card className="border-0 shadow-sm w-100 d-flex flex-column overflow-hidden">
                    <Card.Header className="bg-white py-2 small fw-bold border-bottom d-flex align-items-center">
                        <FaImage className="me-2 text-primary"/> 实时画面
                        <span className="ms-2 text-muted fw-normal">{isPolling ? '轮询中' : status}</span>
                    </Card.Header>
                    <Card.Body className="p-0 d-flex align-items-center justify-content-center bg-light position-relative overflow-hidden">
                        {latestScreenshot && executionId ? (
                            <img 
                                src={`/api/screenshots/${executionId}/${latestScreenshot.split(/[/\\]/).pop()}`} 
                                alt="Live Preview" 
                                style={{maxWidth: '100%', maxHeight: '100%', objectFit: 'contain'}}
                            />
                        ) : (
                            <div className="text-center text-muted">
                                <FaImage size={48} className="mb-2 opacity-25" />
                                <p className="small">暂无截图</p>
                            </div>
                        )}
                    </Card.Body>
                </Card>
            </div>
        </div>
    );
};
