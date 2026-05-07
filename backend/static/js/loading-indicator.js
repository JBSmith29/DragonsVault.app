/**
 * Global loading indicator for HTMX requests
 * Shows a loading bar at the top of the page during HTMX requests
 */

(function () {
  if (window.dvLoadingIndicator) return;

  // Create loading bar element
  const loadingBar = document.createElement('div');
  loadingBar.className = 'dv-loading-bar';
  loadingBar.innerHTML = '<div class="dv-loading-bar-progress"></div>';
  document.body.appendChild(loadingBar);

  // Add styles
  const style = document.createElement('style');
  style.textContent = `
    .dv-loading-bar {
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      height: 3px;
      background: transparent;
      z-index: 9999;
      opacity: 0;
      transition: opacity 0.2s ease;
      pointer-events: none;
    }
    .dv-loading-bar.is-loading {
      opacity: 1;
    }
    .dv-loading-bar-progress {
      height: 100%;
      background: linear-gradient(90deg, 
        var(--bs-primary, #0d6efd) 0%, 
        var(--bs-info, #0dcaf0) 50%, 
        var(--bs-primary, #0d6efd) 100%);
      background-size: 200% 100%;
      animation: loading-shimmer 1.5s ease-in-out infinite;
      width: 0%;
      transition: width 0.3s ease;
    }
    @keyframes loading-shimmer {
      0% { background-position: 200% 0; }
      100% { background-position: -200% 0; }
    }
  `;
  document.head.appendChild(style);

  let activeRequests = 0;
  let progressInterval = null;
  let currentProgress = 0;

  function startLoading() {
    activeRequests++;
    if (activeRequests === 1) {
      currentProgress = 0;
      loadingBar.classList.add('is-loading');
      const progressEl = loadingBar.querySelector('.dv-loading-bar-progress');
      
      // Simulate progress
      progressInterval = setInterval(() => {
        if (currentProgress < 90) {
          currentProgress += Math.random() * 10;
          progressEl.style.width = `${Math.min(currentProgress, 90)}%`;
        }
      }, 200);
    }
  }

  function stopLoading() {
    activeRequests = Math.max(0, activeRequests - 1);
    if (activeRequests === 0) {
      if (progressInterval) {
        clearInterval(progressInterval);
        progressInterval = null;
      }
      
      const progressEl = loadingBar.querySelector('.dv-loading-bar-progress');
      progressEl.style.width = '100%';
      
      setTimeout(() => {
        loadingBar.classList.remove('is-loading');
        setTimeout(() => {
          progressEl.style.width = '0%';
          currentProgress = 0;
        }, 200);
      }, 300);
    }
  }

  // Listen to HTMX events
  document.addEventListener('htmx:beforeRequest', startLoading);
  document.addEventListener('htmx:afterRequest', stopLoading);
  document.addEventListener('htmx:sendError', stopLoading);
  document.addEventListener('htmx:responseError', stopLoading);
  document.addEventListener('htmx:timeout', stopLoading);

  window.dvLoadingIndicator = {
    start: startLoading,
    stop: stopLoading,
  };
})();
