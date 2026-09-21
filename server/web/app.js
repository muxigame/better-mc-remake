'use strict';

const $ = (id) => document.getElementById(id);

async function loadSite() {
  try {
    const response = await fetch('/api/v1/site', { headers: { Accept: 'application/json' } });
    if (!response.ok) throw new Error(`site api ${response.status}`);
    const data = await response.json();
    const pack = data.pack || {};
    const launcher = data.launcher || {};
    $('hero-version').textContent = `${pack.version || 'BMC5'} · Minecraft ${pack.minecraft || '1.21.1'}`;
    $('stat-files').textContent = Number(pack.fileCount || 0).toLocaleString('zh-CN');
    $('stat-size').textContent = pack.sizeText || '—';
    $('server-address').textContent = (data.site || {}).serverAddress || '—';
    $('release-version').textContent = `v${launcher.version || '—'}`;
    $('release-size').textContent = launcher.sizeText || '—';
    $('release-sha').textContent = `SHA-256 ${launcher.sha256 || '—'}`;
    $('download-button').href = launcher.url || '#';
  } catch (error) {
    console.error(error);
    $('release-version').textContent = '暂不可用';
    $('download-button').setAttribute('aria-disabled', 'true');
  }
}

async function loadAccount() {
  const link = $('nav-account');
  if (!link) return;
  try {
    const response = await fetch('/api/v1/auth/me', {
      headers: { Accept: 'application/json' },
      credentials: 'same-origin',
    });
    if (!response.ok) return;
    const data = await response.json();
    const user = data.user || {};
    const nickname = user.nickname || user.username;
    if (!nickname) return;
    link.textContent = nickname;
    link.title = `@${user.username || ''} · UID ${user.uid || '—'}`;
    link.classList.add('is-authenticated');
  } catch (error) {
    console.debug('account state unavailable', error);
  }
}

loadSite();
loadAccount();