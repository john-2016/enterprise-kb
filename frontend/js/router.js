/* Phase 6 — Hash router with admin auth guard.
 * Routes:
 *   #/chat            → showPage('chat')  (default)
 *   #/admin/models    → renderAdminModels()
 *   #/admin/ab-tests  → renderAdminABTests()
 *   #/admin/metrics   → renderAdminMetrics()
 * Auth: any /admin/* requires role==admin via /api/v1/auth/me.
 * Non-admin → redirect to #/chat.
 */
(function () {
  const ROUTES = {
    '#/chat':           { page: 'chat',          render: null },
    '#/admin/models':   { page: 'admin-models',  render: () => window.AdminModels && window.AdminModels.render() },
    '#/admin/ab-tests': { page: 'admin-ab-tests', render: () => window.AdminABTests && window.AdminABTests.render() },
    '#/admin/metrics':  { page: 'admin-metrics', render: () => window.AdminMetrics && window.AdminMetrics.render() },
  };

  function currentRoute() {
    const h = window.location.hash || '#/chat';
    return ROUTES[h] ? h : '#/chat';
  }

  // Deactivate all .page and clear nav active class
  function clearAll() {
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  }

  // Highlight sidebar nav-item whose data-route matches (or fall back to data-page)
  function highlightNav(route) {
    const sel = `[data-route="${route}"]`;
    const navEl = document.querySelector(sel);
    if (navEl) {
      navEl.classList.add('active');
      return;
    }
    // fallback for legacy pages
    const legacyMap = { '#/chat': 'chat' };
    const pageName = legacyMap[route];
    if (pageName) {
      const el = document.querySelector(`.nav-item[data-page="${pageName}"]:not([data-route])`);
      if (el) el.classList.add('active');
    }
  }

  // Admin auth check: returns true if user is admin, otherwise redirects.
  async function ensureAdmin() {
    try {
      const me = await api('/auth/me');
      if (!me) { window.location.hash = '#/chat'; return false; }
      state.user = me;
      if (me.role !== 'admin') {
        toast('需要管理员权限', 'error');
        window.location.hash = '#/chat';
        return false;
      }
      return true;
    } catch (e) {
      window.location.hash = '#/chat';
      return false;
    }
  }

  async function handleRoute() {
    const route = currentRoute();
    clearAll();

    // Show admin nav items only when user is admin (best-effort, non-blocking)
    if (state.user && state.user.role === 'admin') {
      document.querySelectorAll('.nav-item[data-page="ab-tests"], .nav-item[data-page="metrics"]')
        .forEach(el => el.style.display = '');
    }

    if (route.startsWith('#/admin')) {
      const ok = await ensureAdmin();
      if (!ok) return;
    }

    const def = ROUTES[route];
    highlightNav(route);
    const page = document.getElementById(`page-${def.page}`);
    if (page) page.classList.add('active');

    if (def.render) {
      try { await def.render(); }
      catch (e) {
        console.error('render error', e);
        toast('页面加载失败: ' + e.message, 'error');
      }
    }

    // Chat page needs scroll
    if (def.page === 'chat' && typeof scrollChat === 'function') scrollChat();
  }

  // Public: navigate to a route programmatically
  window.navigateTo = function (route) {
    if (!ROUTES[route]) route = '#/chat';
    if (window.location.hash === route) {
      handleRoute();
    } else {
      window.location.hash = route;
    }
  };

  window.addEventListener('hashchange', handleRoute);

  // Hook into loadApp: after auth, set default route + first render
  const _origLoadApp = window.loadApp;
  window.loadApp = async function () {
    await _origLoadApp();
    // Show admin nav items
    if (state.user && state.user.role === 'admin') {
      document.querySelectorAll('.nav-item[data-page="ab-tests"], .nav-item[data-page="metrics"]')
        .forEach(el => el.style.display = '');
    } else {
      // hide them
      document.querySelectorAll('.nav-item[data-page="ab-tests"], .nav-item[data-page="metrics"]')
        .forEach(el => el.style.display = 'none');
    }
    if (!window.location.hash) window.location.hash = '#/chat';
    handleRoute();
  };

  // If app already loaded (no token case won't reach here, but be safe):
  if (document.readyState === 'complete' || document.readyState === 'interactive') {
    // No-op; loadApp() will call handleRoute()
  }
})();