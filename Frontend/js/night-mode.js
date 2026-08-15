/* Night mode — Tracking page only (map + theft simulation) */
(function () {
  const KEY = 'agriguard_night';

  function isTrackingPage() {
    return document.body.classList.contains('tracking-page')
      || /tracking\.html$/i.test(location.pathname);
  }

  function label(night) {
    return night
      ? '<svg class="ag-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg> Day Mode'
      : '<svg class="ag-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 14.5A8.5 8.5 0 0110.5 3 7 7 0 1021 14.5z"/></svg> Night Mode';
  }

  function apply(night) {
    if (!isTrackingPage()) {
      document.body.classList.remove('night');
      return;
    }
    document.body.classList.toggle('night', !!night);
    localStorage.setItem(KEY, night ? '1' : '0');
    document.querySelectorAll('#nightBtn, .night-btn').forEach(btn => {
      btn.innerHTML = label(night);
      btn.setAttribute('aria-pressed', night ? 'true' : 'false');
    });
    if (typeof window.__agriguardOnNightChange === 'function') {
      try { window.__agriguardOnNightChange(!!night); } catch (_) {}
    }
  }

  window.toggleNight = function () {
    if (!isTrackingPage()) return;
    apply(!document.body.classList.contains('night'));
  };

  window.applyNight = apply;

  function init() {
    if (!isTrackingPage()) {
      document.body.classList.remove('night');
      return;
    }
    apply(localStorage.getItem(KEY) === '1');
  }

  document.addEventListener('DOMContentLoaded', init);
  if (document.readyState !== 'loading') init();
})();
