/**
 * Enhanced error handling with user-friendly messages and retry logic
 * Provides better feedback for common error scenarios
 */

(function () {
  if (window.dvErrorHandler) return;

  const ERROR_MESSAGES = {
    network: {
      title: 'Connection Error',
      message: 'Unable to connect to the server. Please check your internet connection.',
      action: 'Retry',
    },
    timeout: {
      title: 'Request Timeout',
      message: 'The request took too long to complete. Please try again.',
      action: 'Retry',
    },
    unauthorized: {
      title: 'Authentication Required',
      message: 'Your session has expired. Please log in again.',
      action: 'Log In',
    },
    forbidden: {
      title: 'Access Denied',
      message: 'You don\'t have permission to perform this action.',
      action: null,
    },
    notFound: {
      title: 'Not Found',
      message: 'The requested resource could not be found.',
      action: null,
    },
    rateLimit: {
      title: 'Too Many Requests',
      message: 'You\'re making requests too quickly. Please wait a moment and try again.',
      action: 'Retry',
    },
    serverError: {
      title: 'Server Error',
      message: 'Something went wrong on our end. We\'ve been notified and are working on it.',
      action: 'Retry',
    },
    validation: {
      title: 'Validation Error',
      message: 'Please check your input and try again.',
      action: null,
    },
  };

  function getErrorType(status, error) {
    if (!navigator.onLine) return 'network';
    if (status === 0 || error?.name === 'NetworkError') return 'network';
    if (error?.name === 'TimeoutError') return 'timeout';
    if (status === 401) return 'unauthorized';
    if (status === 403) return 'forbidden';
    if (status === 404) return 'notFound';
    if (status === 429) return 'rateLimit';
    if (status === 422 || status === 400) return 'validation';
    if (status >= 500) return 'serverError';
    return 'serverError';
  }

  function showErrorToast(type, customMessage = null, retryFn = null) {
    const errorInfo = ERROR_MESSAGES[type] || ERROR_MESSAGES.serverError;
    const message = customMessage || errorInfo.message;

    // Create toast element
    const toast = document.createElement('div');
    toast.className = 'toast dv-error-toast';
    toast.setAttribute('role', 'alert');
    toast.setAttribute('aria-live', 'assertive');
    toast.setAttribute('aria-atomic', 'true');
    
    const actionButton = errorInfo.action && retryFn
      ? `<button type="button" class="btn btn-sm btn-light ms-2" data-action="retry">${errorInfo.action}</button>`
      : '';

    toast.innerHTML = `
      <div class="toast-header bg-danger text-white">
        <svg class="me-2" width="16" height="16" fill="currentColor" viewBox="0 0 16 16">
          <path d="M8 15A7 7 0 1 1 8 1a7 7 0 0 1 0 14zm0 1A8 8 0 1 0 8 0a8 8 0 0 0 0 16z"/>
          <path d="M7.002 11a1 1 0 1 1 2 0 1 1 0 0 1-2 0zM7.1 4.995a.905.905 0 1 1 1.8 0l-.35 3.507a.552.552 0 0 1-1.1 0L7.1 4.995z"/>
        </svg>
        <strong class="me-auto">${errorInfo.title}</strong>
        <button type="button" class="btn-close btn-close-white" data-bs-dismiss="toast" aria-label="Close"></button>
      </div>
      <div class="toast-body">
        ${message}
        ${actionButton}
      </div>
    `;

    // Add to container
    let container = document.querySelector('.dv-toast-container');
    if (!container) {
      container = document.createElement('div');
      container.className = 'dv-toast-container position-fixed top-0 end-0 p-3';
      container.style.zIndex = '9999';
      document.body.appendChild(container);
    }
    container.appendChild(toast);

    // Handle retry button
    if (retryFn) {
      const retryBtn = toast.querySelector('[data-action="retry"]');
      if (retryBtn) {
        retryBtn.addEventListener('click', () => {
          const bsToast = bootstrap.Toast.getInstance(toast);
          if (bsToast) bsToast.hide();
          retryFn();
        });
      }
    }

    // Show toast
    const bsToast = new bootstrap.Toast(toast, {
      autohide: type === 'validation' || type === 'rateLimit',
      delay: 5000,
    });
    bsToast.show();

    // Remove from DOM after hidden
    toast.addEventListener('hidden.bs.toast', () => {
      toast.remove();
    });

    return toast;
  }

  // Handle HTMX errors
  document.addEventListener('htmx:responseError', (event) => {
    const { detail } = event;
    const status = detail.xhr?.status || 0;
    const errorType = getErrorType(status, detail.error);

    // Try to get error message from response
    let customMessage = null;
    try {
      const response = JSON.parse(detail.xhr?.responseText || '{}');
      customMessage = response.detail || response.error || response.message;
    } catch (e) {
      // Ignore parse errors
    }

    // Show error toast with retry
    showErrorToast(errorType, customMessage, () => {
      // Retry the request
      if (detail.target) {
        htmx.trigger(detail.target, 'htmx:retry');
      }
    });
  });

  document.addEventListener('htmx:sendError', (event) => {
    const { detail } = event;
    showErrorToast('network', null, () => {
      if (detail.target) {
        htmx.trigger(detail.target, 'htmx:retry');
      }
    });
  });

  document.addEventListener('htmx:timeout', (event) => {
    const { detail } = event;
    showErrorToast('timeout', null, () => {
      if (detail.target) {
        htmx.trigger(detail.target, 'htmx:retry');
      }
    });
  });

  // Handle fetch errors globally
  const originalFetch = window.fetch;
  window.fetch = async function (...args) {
    try {
      const response = await originalFetch.apply(this, args);
      
      // Handle error responses
      if (!response.ok) {
        const errorType = getErrorType(response.status);
        
        // For 401, redirect to login
        if (response.status === 401) {
          const currentPath = window.location.pathname;
          if (currentPath !== '/login') {
            window.location.href = `/login?next=${encodeURIComponent(currentPath)}`;
          }
        }
      }
      
      return response;
    } catch (error) {
      console.error('[ErrorHandler] Fetch error:', error);
      throw error;
    }
  };

  // Add styles
  const style = document.createElement('style');
  style.textContent = `
    .dv-error-toast {
      min-width: 300px;
      max-width: 500px;
    }
    .dv-error-toast .toast-body {
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
  `;
  document.head.appendChild(style);

  window.dvErrorHandler = {
    show: showErrorToast,
    getErrorType,
  };
})();
