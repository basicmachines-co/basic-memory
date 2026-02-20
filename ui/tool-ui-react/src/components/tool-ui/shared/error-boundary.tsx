"use client";

import * as React from "react";

export interface ToolUIErrorBoundaryProps {
  componentName: string;
  children: React.ReactNode;
  fallback?: React.ReactNode;
  onError?: (error: Error, errorInfo: React.ErrorInfo) => void;
}

interface ToolUIErrorBoundaryState {
  hasError: boolean;
  error?: Error;
}

export class ToolUIErrorBoundary extends React.Component<
  ToolUIErrorBoundaryProps,
  ToolUIErrorBoundaryState
> {
  constructor(props: ToolUIErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(error: Error): ToolUIErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error(`[${this.props.componentName}] render error:`, error, errorInfo);
    this.props.onError?.(error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        this.props.fallback ?? (
          <div className="border-destructive text-destructive rounded-lg border p-4">
            <p className="font-semibold">
              {this.props.componentName} failed to render
            </p>
            <p className="text-sm">{this.state.error?.message}</p>
          </div>
        )
      );
    }
    return this.props.children;
  }
}

