import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("Uncaught error:", error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
            display: 'flex', 
            flexDirection: 'column', 
            alignItems: 'center', 
            justifyContent: 'center', 
            height: '100vh', 
            backgroundColor: '#f8f9fa', 
            color: '#dc3545', 
            padding: '2rem',
            fontFamily: 'system-ui, -apple-system, sans-serif'
        }}>
            <h1 style={{fontSize: '2rem', marginBottom: '1rem'}}>Application Error</h1>
            <pre style={{
                backgroundColor: 'white', 
                padding: '1rem', 
                borderRadius: '0.5rem', 
                boxShadow: '0 2px 4px rgba(0,0,0,0.1)', 
                marginBottom: '1.5rem',
                maxWidth: '800px',
                overflow: 'auto'
            }}>
                {this.state.error?.toString()}
            </pre>
            <button 
                onClick={() => window.location.reload()}
                style={{
                    padding: '0.5rem 1rem',
                    fontSize: '1rem',
                    color: 'white',
                    backgroundColor: '#0d6efd',
                    border: 'none',
                    borderRadius: '0.25rem',
                    cursor: 'pointer'
                }}
            >
                Reload Page
            </button>
        </div>
      );
    }

    return this.props.children;
  }
}
