import React from 'react';
import Editor from '@monaco-editor/react';
import { Card } from 'react-bootstrap';

interface ScriptEditorProps {
    script: string;
    onChange: (value: string) => void;
    readOnly?: boolean;
}

export const ScriptEditor: React.FC<ScriptEditorProps> = ({ script, onChange, readOnly = false }) => {
    const monacoTheme = typeof document !== 'undefined' && document.body.classList.contains('theme-dark') ? 'vs-dark' : 'light';

    return (
        <Card className="ui-automation-script-card h-100 border-0 d-flex flex-column">
            <div className="flex-grow-1 overflow-hidden">
                <Editor
                    height="100%"
                    defaultLanguage="python"
                    value={script}
                    theme={monacoTheme}
                    onChange={(value) => onChange(value || '')}
                    options={{
                        minimap: { enabled: false },
                        scrollBeyondLastLine: false,
                        fontSize: 12,
                        readOnly,
                        automaticLayout: true,
                    }}
                />
            </div>
        </Card>
    );
};
