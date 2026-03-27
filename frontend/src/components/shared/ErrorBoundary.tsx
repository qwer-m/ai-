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
    console.error('Uncaught error:', error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="error-boundary-shell">
          <h1 className="error-boundary-title">Application Error</h1>
          <pre className="error-boundary-stack">{this.state.error?.toString()}</pre>
          <button onClick={() => window.location.reload()} className="error-boundary-reload-btn">
            Reload Page
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}
