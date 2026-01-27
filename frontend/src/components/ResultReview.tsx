import React, { useState } from 'react';
import { Container, Table, Badge, Button } from 'react-bootstrap';
import { FaCheck, FaTimes, FaSave } from 'react-icons/fa';

interface ReviewItem {
    id: string;
    api: string;
    result: 'Normal' | 'Abnormal' | 'False Positive' | 'False Negative';
    reason: string;
}

export const ResultReview: React.FC = () => {
    const [items] = useState<ReviewItem[]>([
        { id: '1', api: 'GET /users', result: 'Normal', reason: 'Status 200 matches expectation' },
        { id: '2', api: 'POST /login', result: 'False Positive', reason: '400 Bad Request expected for invalid input' }
    ]);

    const handleSaveToRag = () => {
        alert('Saving verified items to Knowledge Base... (Backend integration pending)');
        // Call backend API to save
    };

    return (
        <Container fluid className="p-3 bg-white h-100 d-flex flex-column">
            <div className="d-flex justify-content-between align-items-center mb-3">
                <h5 className="mb-0">结果审核与知识入库 (RAG Review)</h5>
                <Button variant="success" onClick={handleSaveToRag}><FaSave className="me-2"/>保存至知识库</Button>
            </div>
            <div className="flex-grow-1 overflow-auto">
                <Table striped hover size="sm">
                    <thead>
                        <tr>
                            <th>API</th>
                            <th>AI 判定结果</th>
                            <th>原因</th>
                            <th>人工确认</th>
                        </tr>
                    </thead>
                    <tbody>
                        {items.map(item => (
                            <tr key={item.id}>
                                <td>{item.api}</td>
                                <td>
                                    <Badge bg={
                                        item.result === 'Normal' ? 'success' : 
                                        item.result === 'Abnormal' ? 'danger' : 'warning'
                                    } text={item.result.includes('False') ? 'dark' : 'white'}>{item.result}</Badge>
                                </td>
                                <td className="small text-muted">{item.reason}</td>
                                <td>
                                    <div className="d-flex gap-2">
                                        <Button size="sm" variant="outline-success" title="Confirm Correct"><FaCheck/></Button>
                                        <Button size="sm" variant="outline-danger" title="Mark Incorrect"><FaTimes/></Button>
                                    </div>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </Table>
            </div>
        </Container>
    );
};
