'use strict';
(() => {
  function entryUrl(returnTo = '/account.html') {
    return '/api/v1/auth/entry?return_to=' + encodeURIComponent(returnTo);
  }
  async function refresh() {
    // Links work without JavaScript and always check the live session on click.
    document.querySelectorAll('[data-auth-return="current"]').forEach(link => {
      link.href = entryUrl(location.pathname + location.search + location.hash);
    });
    try {
      const response = await fetch('/api/v1/auth/me', {
        credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' },
      });
      if (!response.ok && response.status !== 401) return;
      const user = response.ok ? (await response.json()).user : null;
      document.querySelectorAll('[data-account-link]').forEach(link => {
        link.textContent = user ? (user.nickname || user.username || '玩家中心') : '玩家账号';
        link.title = user ? '@' + user.username + ' · UID ' + user.uid : '使用 Muxi Account 登录';
        link.classList.toggle('is-authenticated', !!user);
      });
    } catch (_) { /* Keep the working server-side entry link when offline. */ }
  }
  window.BmcAuth = { entryUrl, refresh };
  refresh();
  window.addEventListener('pageshow', event => { if (event.persisted) refresh(); });
})();