/* ==========================================================================
   Mera — mockup behaviour
   Icon sprite, theme switching, rail toggle, command palette, demo controls.
   Injected rather than linked so the files work opened directly from disk.
   ========================================================================== */

(function () {
  'use strict';

  /* --- Icons -------------------------------------------------------------
     One 16x16 grid, 1.5px stroke, round caps and joins. Platform marks are
     filled glyphs and opt out of the stroke via .pm.
     --------------------------------------------------------------------- */

  var UI_ICONS = {
    dashboard: '<rect x="2" y="2" width="5.5" height="5.5" rx="1"/><rect x="10.5" y="2" width="5.5" height="5.5" rx="1"/><rect x="2" y="10.5" width="5.5" height="5.5" rx="1"/><rect x="10.5" y="10.5" width="5.5" height="5.5" rx="1"/>',
    compose: '<path d="M11.6 2.6a1.7 1.7 0 0 1 2.4 2.4L6.3 12.7l-3.1.9.9-3.1z"/><path d="M10.2 4l2.4 2.4"/>',
    approvals: '<path d="M4 8.6 6.8 11.4 12.4 5.8"/><rect x="1.8" y="1.8" width="12.4" height="12.4" rx="3"/>',
    calendar: '<rect x="2.2" y="3.2" width="11.6" height="11" rx="2"/><path d="M2.2 6.6h11.6M5.5 1.8v2.6M10.5 1.8v2.6"/>',
    inbox: '<path d="M2 9.4h3.2l1 1.8h3.6l1-1.8H14"/><path d="M3.6 3h8.8l1.6 6.4v3.1a1.5 1.5 0 0 1-1.5 1.5H3.5A1.5 1.5 0 0 1 2 12.5V9.4z"/>',
    analytics: '<path d="M2.2 13.8h11.6"/><path d="M4.4 11.4V7.6M8 11.4V3.2M11.6 11.4V9"/>',
    sources: '<path d="M3 13a0 0 0 0 0 0 0"/><circle cx="3.4" cy="12.6" r="1.1" fill="currentColor" stroke="none"/><path d="M2.6 8.2a5.2 5.2 0 0 1 5.2 5.2M2.6 3.6A9.8 9.8 0 0 1 12.4 13.4"/>',
    discovery: '<circle cx="8" cy="8" r="6.2"/><path d="m10.6 5.4-1.3 3.9-3.9 1.3 1.3-3.9z"/>',
    accounts: '<path d="M6.4 2.6 3.1 5.9a3.6 3.6 0 0 0 5.1 5.1l1.2-1.2"/><path d="M9.6 13.4l3.3-3.3a3.6 3.6 0 0 0-5.1-5.1L6.6 6.2"/>',
    groups: '<path d="M8 1.8 14.4 5 8 8.2 1.6 5z"/><path d="m1.6 8 6.4 3.2L14.4 8"/><path d="m1.6 11 6.4 3.2L14.4 11"/>',
    agents: '<rect x="3" y="5.4" width="10" height="8" rx="2"/><path d="M8 5.4V3M6.2 9h.01M9.8 9h.01M6.4 11.4h3.2"/><circle cx="8" cy="2.2" r="1"/>',
    mcp: '<path d="M5.4 2.4v3.2M10.6 2.4v3.2"/><rect x="3.2" y="5.6" width="9.6" height="4.6" rx="1.6"/><path d="M8 10.2v3.4"/>',
    audit: '<path d="M8 1.9 13.4 4v4c0 3.2-2.2 5.4-5.4 6.1C4.8 13.4 2.6 11.2 2.6 8V4z"/><path d="M5.9 8.1 7.3 9.5l3-3"/>',
    settings: '<circle cx="8" cy="8" r="2.2"/><path d="M12.9 9.8a1.2 1.2 0 0 0 .24 1.33l.04.05a1.46 1.46 0 1 1-2.06 2.06l-.05-.04a1.2 1.2 0 0 0-1.33-.24 1.2 1.2 0 0 0-.73 1.1v.13a1.46 1.46 0 1 1-2.92 0v-.07a1.2 1.2 0 0 0-.78-1.1 1.2 1.2 0 0 0-1.33.24l-.05.04a1.46 1.46 0 1 1-2.06-2.06l.04-.05a1.2 1.2 0 0 0 .24-1.33 1.2 1.2 0 0 0-1.1-.73h-.13a1.46 1.46 0 1 1 0-2.92h.07a1.2 1.2 0 0 0 1.1-.78 1.2 1.2 0 0 0-.24-1.33l-.04-.05a1.46 1.46 0 1 1 2.06-2.06l.05.04a1.2 1.2 0 0 0 1.33.24h.06a1.2 1.2 0 0 0 .73-1.1v-.13a1.46 1.46 0 1 1 2.92 0v.07a1.2 1.2 0 0 0 .73 1.1 1.2 1.2 0 0 0 1.33-.24l.05-.04a1.46 1.46 0 1 1 2.06 2.06l-.04.05a1.2 1.2 0 0 0-.24 1.33v.06a1.2 1.2 0 0 0 1.1.73h.13a1.46 1.46 0 1 1 0 2.92h-.07a1.2 1.2 0 0 0-1.1.73z"/>',
    voice: '<path d="M13.6 9.6a1.6 1.6 0 0 1-1.6 1.6H5.6L2.4 14V3.6A1.6 1.6 0 0 1 4 2h8a1.6 1.6 0 0 1 1.6 1.6z"/><path d="M6 6.4h4M6 8.8h2.4"/>',
    automation: '<path d="M8.8 1.8 3.2 9.2h4l-.8 5.2 5.6-7.4h-4z"/>',
    channels: '<path d="M11.6 6.4a3.6 3.6 0 1 0-7.2 0c0 4.2-1.8 5.4-1.8 5.4h10.8s-1.8-1.2-1.8-5.4"/><path d="M9.1 13.8a1.3 1.3 0 0 1-2.2 0"/>',
    team: '<circle cx="6" cy="5.4" r="2.6"/><path d="M1.8 13.6a4.4 4.4 0 0 1 8.4 0"/><path d="M11 3.1a2.6 2.6 0 0 1 0 4.6M12.4 13.6a4.4 4.4 0 0 0-1.6-3.4"/>',
    plus: '<path d="M8 3.2v9.6M3.2 8h9.6"/>',
    search: '<circle cx="7.2" cy="7.2" r="4.6"/><path d="m10.6 10.6 3 3"/>',
    filter: '<path d="M2.4 3.4h11.2l-4.3 5v4.6l-2.6 1.2V8.4z"/>',
    chevdown: '<path d="m4 6.2 4 4 4-4"/>',
    chevright: '<path d="m6.2 4 4 4-4 4"/>',
    more: '<circle cx="3.4" cy="8" r="1.1" fill="currentColor" stroke="none"/><circle cx="8" cy="8" r="1.1" fill="currentColor" stroke="none"/><circle cx="12.6" cy="8" r="1.1" fill="currentColor" stroke="none"/>',
    external: '<path d="M9.4 2.6h4v4"/><path d="M13.4 2.6 7.6 8.4"/><path d="M12 9.6v2.8a1.4 1.4 0 0 1-1.4 1.4H3.6a1.4 1.4 0 0 1-1.4-1.4V5.4A1.4 1.4 0 0 1 3.6 4h2.8"/>',
    refresh: '<path d="M13.6 6.8A5.8 5.8 0 0 0 3.3 4.5L2.4 5.4"/><path d="M2.4 9.2a5.8 5.8 0 0 0 10.3 2.3l.9-.9"/><path d="M2.4 2.6v2.8h2.8M13.6 13.4v-2.8h-2.8"/>',
    trash: '<path d="M2.6 4.2h10.8"/><path d="M5.4 4.2V3a1.2 1.2 0 0 1 1.2-1.2h2.8A1.2 1.2 0 0 1 10.6 3v1.2"/><path d="M12.2 4.2v8.6a1.4 1.4 0 0 1-1.4 1.4H5.2a1.4 1.4 0 0 1-1.4-1.4V4.2"/>',
    edit: '<path d="M7.4 2.9H3.3a1.5 1.5 0 0 0-1.5 1.5v8.3a1.5 1.5 0 0 0 1.5 1.5h8.3a1.5 1.5 0 0 0 1.5-1.5V8.6"/><path d="M11.7 1.9a1.6 1.6 0 0 1 2.3 2.3L8.3 9.9l-2.9.6.6-2.9z"/>',
    image: '<rect x="2.2" y="2.9" width="11.6" height="10.2" rx="1.8"/><circle cx="6" cy="6.4" r="1.2"/><path d="m2.6 11.4 3-2.8a1.4 1.4 0 0 1 1.9 0l3.4 3.2"/><path d="m9.8 9.6 1-1a1.4 1.4 0 0 1 1.9 0l1.1 1"/>',
    video: '<rect x="1.8" y="4" width="9.4" height="8" rx="1.8"/><path d="m11.2 8.6 3-2v5.2l-3-2z"/>',
    clock: '<circle cx="8" cy="8" r="6.2"/><path d="M8 4.4V8l2.4 1.4"/>',
    alert: '<path d="M7 2.4a1.2 1.2 0 0 1 2 0l5 8.8a1.2 1.2 0 0 1-1 1.8H3a1.2 1.2 0 0 1-1-1.8z"/><path d="M8 6.2v2.6M8 11.2h.01"/>',
    check: '<path d="m3.4 8.4 3.1 3.1 6.1-6.9"/>',
    x: '<path d="M4 4l8 8M12 4l-8 8"/>',
    arrow: '<path d="M3.2 8h9.6M9 4.2 12.8 8 9 11.8"/>',
    power: '<path d="M8 2.4v5.8"/><path d="M11.8 4.5a5.4 5.4 0 1 1-7.6 0"/>',
    sun: '<circle cx="8" cy="8" r="3.1"/><path d="M8 1.4v1.6M8 13v1.6M3.3 3.3l1.1 1.1M11.6 11.6l1.1 1.1M1.4 8h1.6M13 8h1.6M3.3 12.7l1.1-1.1M11.6 4.4l1.1-1.1"/>',
    moon: '<path d="M13.4 9.2A5.8 5.8 0 0 1 6.2 2a6.2 6.2 0 1 0 7.2 7.2"/>',
    menu: '<path d="M2.4 4.4h11.2M2.4 8h11.2M2.4 11.6h11.2"/>',
    info: '<circle cx="8" cy="8" r="6.2"/><path d="M8 7.4v3.4M8 5.3h.01"/>',
    upload: '<path d="M13.4 10.2v2.2a1.4 1.4 0 0 1-1.4 1.4H4a1.4 1.4 0 0 1-1.4-1.4v-2.2"/><path d="M11.2 5.8 8 2.6 4.8 5.8M8 2.6v7.6"/>',
    play: '<path d="M4.6 2.9 13 8l-8.4 5.1z"/>',
    pause: '<path d="M5.8 3.2v9.6M10.2 3.2v9.6"/>',
    copy: '<rect x="5.4" y="5.4" width="8.4" height="8.4" rx="1.6"/><path d="M11 5.4V3.8a1.6 1.6 0 0 0-1.6-1.6H3.8a1.6 1.6 0 0 0-1.6 1.6v5.6A1.6 1.6 0 0 0 3.8 11h1.6"/>',
    link: '<path d="M6.6 8.6a2.8 2.8 0 0 0 4.2.3l1.7-1.7a2.8 2.8 0 0 0-4-4l-1 1"/><path d="M9.4 7.4a2.8 2.8 0 0 0-4.2-.3L3.5 8.8a2.8 2.8 0 0 0 4 4l1-1"/>',
    stagger: '<path d="M2.4 4h5.2M2.4 8h8.4M2.4 12h11.2"/><circle cx="8.6" cy="4" r="1.1" fill="currentColor" stroke="none"/><circle cx="11.8" cy="8" r="1.1" fill="currentColor" stroke="none"/>',
    hash: '<path d="M6.2 2.4 4.8 13.6M11.2 2.4 9.8 13.6M2.6 5.8h11M2 10.2h11"/>',
    bolt: '<path d="M8 1.8 2.8 8.6h4L8 14.2l5.2-6.8h-4z"/>',
    dl: '<path d="M8 2.6v7.8M4.8 7.2 8 10.4l3.2-3.2"/><path d="M2.6 13.4h10.8"/>'
  };

  var PLATFORM_ICONS = {
    linkedin: '<path d="M3.4 5.9h2.4v7.7H3.4zM4.6 2.4a1.4 1.4 0 1 1 0 2.8 1.4 1.4 0 0 1 0-2.8zM7.4 5.9h2.3v1.05h.03c.32-.6 1.1-1.24 2.27-1.24 2.43 0 2.88 1.57 2.88 3.62v4.27h-2.4V9.8c0-.88-.02-2.02-1.24-2.02-1.24 0-1.43.95-1.43 1.95v3.87H7.4z"/>',
    x: '<path d="M9.5 6.9 14 1.8h-1.07L9.03 6.2 5.9 1.8H2.3l4.7 6.6-4.7 5.3h1.07L7.5 8.8l3.3 4.9h3.6zM8.05 8.2l-.45-.63L3.76 2.6h1.64l2.9 4.07.45.63 3.8 5.33h-1.64z"/>',
    facebook: '<path d="M14 8a6 6 0 1 0-6.94 5.93V9.73H5.54V8h1.52V6.69c0-1.5.9-2.33 2.26-2.33.65 0 1.34.12 1.34.12v1.47h-.76c-.74 0-.97.46-.97.94V8h1.66l-.27 1.73H8.93v4.2A6 6 0 0 0 14 8"/>',
    instagram: '<path d="M8 3.24c1.56 0 1.74.01 2.36.04.57.02.88.12 1.08.2.27.1.47.23.67.43.2.2.33.4.43.67.08.2.18.51.2 1.08.03.62.04.8.04 2.36s-.01 1.74-.04 2.36c-.02.57-.12.88-.2 1.08-.1.27-.23.47-.43.67-.2.2-.4.33-.67.43-.2.08-.51.18-1.08.2-.62.03-.8.04-2.36.04s-1.74-.01-2.36-.04c-.57-.02-.88-.12-1.08-.2a1.8 1.8 0 0 1-.67-.43 1.8 1.8 0 0 1-.43-.67c-.08-.2-.18-.51-.2-1.08-.03-.62-.04-.8-.04-2.36s.01-1.74.04-2.36c.02-.57.12-.88.2-1.08.1-.27.23-.47.43-.67.2-.2.4-.33.67-.43.2-.08.51-.18 1.08-.2.62-.03.8-.04 2.36-.04M8 2.19c-1.58 0-1.78.01-2.4.04-.63.03-1.05.13-1.42.27-.39.15-.72.35-1.05.68-.33.33-.53.66-.68 1.05-.14.37-.24.79-.27 1.42-.03.62-.04.82-.04 2.4s.01 1.78.04 2.4c.03.63.13 1.05.27 1.42.15.39.35.72.68 1.05.33.33.66.53 1.05.68.37.14.79.24 1.42.27.62.03.82.04 2.4.04s1.78-.01 2.4-.04c.63-.03 1.05-.13 1.42-.27.39-.15.72-.35 1.05-.68.33-.33.53-.66.68-1.05.14-.37.24-.79.27-1.42.03-.62.04-.82.04-2.4s-.01-1.78-.04-2.4c-.03-.63-.13-1.05-.27-1.42a2.85 2.85 0 0 0-.68-1.05 2.85 2.85 0 0 0-1.05-.68c-.37-.14-.79-.24-1.42-.27-.62-.03-.82-.04-2.4-.04"/><path d="M8 5.02a2.98 2.98 0 1 0 0 5.96 2.98 2.98 0 0 0 0-5.96m0 4.91a1.93 1.93 0 1 1 0-3.86 1.93 1.93 0 0 1 0 3.86"/><circle cx="11.1" cy="4.9" r=".7"/>',
    threads: '<path d="M11.2 7.5a4.3 4.3 0 0 0-.17-.08c-.1-1.85-1.11-2.9-2.8-2.92h-.03c-1.01 0-1.85.43-2.37 1.21l.93.64c.39-.59.99-.71 1.44-.71h.02c.56 0 .99.17 1.26.49.2.23.34.56.4.97a7.3 7.3 0 0 0-1.63-.08c-1.64.1-2.7 1.05-2.63 2.38.04.67.37 1.25.94 1.63.48.32 1.1.48 1.75.44.85-.04 1.52-.37 1.98-.97.35-.46.57-1.05.67-1.8.4.24.69.56.86.95.28.66.3 1.75-.59 2.63-.78.78-1.71 1.11-3.12 1.12-1.57-.01-2.75-.51-3.52-1.49-.72-.91-1.09-2.23-1.1-3.92.01-1.69.38-3 1.1-3.92C5.36 3.1 6.55 2.6 8.11 2.59c1.58.01 2.79.51 3.59 1.5.4.48.69 1.09.89 1.8l1.09-.29a6.1 6.1 0 0 0-1.13-2.22C11.52 2.11 10.02 1.47 8.11 1.46h-.01c-1.9.01-3.38.65-4.4 1.91C2.8 4.49 2.33 6.05 2.31 7.99v.02c.02 1.94.49 3.5 1.39 4.62 1.02 1.26 2.5 1.9 4.4 1.91h.01c1.69-.01 2.88-.46 3.86-1.44 1.29-1.28 1.25-2.89.82-3.88-.3-.71-.88-1.29-1.67-1.72M8.2 10.4c-.72.04-1.47-.28-1.51-.95-.03-.5.35-1.05 1.56-1.12.14 0 .27-.01.4-.01.44 0 .85.04 1.22.12-.14 1.72-.95 1.92-1.67 1.96"/>',
    bluesky: '<path d="M4.2 3.1c1.6 1.2 3.3 3.6 4 4.9.7-1.3 2.4-3.7 4-4.9 1.15-.86 3-1.53 3 .6 0 .42-.24 3.56-.38 4.07-.49 1.77-2.3 2.22-3.9 1.95 2.8.48 3.52 2.06 1.98 3.64-2.92 3-4.2-.75-4.52-1.71-.06-.18-.09-.26-.09-.19 0-.07-.03.01-.09.19-.32.96-1.6 4.71-4.52 1.71-1.54-1.58-.82-3.16 1.98-3.64-1.6.27-3.41-.18-3.9-1.95C1.44 7.26 1.2 4.12 1.2 3.7c0-2.13 1.85-1.46 3-.6"/>',
    mastodon: '<path d="M14 5.9c0-2.7-1.77-3.5-1.77-3.5-.9-.41-2.43-.58-4.02-.6h-.04c-1.6.01-3.13.19-4.02.6 0 0-1.77.8-1.77 3.5l-.01 1.3c0 2.66.17 5.28 2.6 5.93 1.13.3 2.1.36 2.87.32 1.4-.08 2.19-.5 2.19-.5l-.05-1.02s-1 .32-2.13.28c-1.12-.04-2.3-.12-2.48-1.5a2.8 2.8 0 0 1-.02-.38s1.1.27 2.5.33c.85.04 1.65-.05 2.46-.15 1.56-.18 2.92-1.15 3.09-2.03.27-1.38.25-3.38.25-3.38M11.8 9.2h-1.37V5.86c0-.7-.3-1.06-.89-1.06-.65 0-.98.42-.98 1.26v1.82H7.2V6.06c0-.84-.33-1.26-.98-1.26-.6 0-.89.36-.89 1.06V9.2H3.95V5.76c0-.7.18-1.26.54-1.67.37-.42.85-.63 1.45-.63.7 0 1.22.27 1.57.8L8 4.9l.49-.64c.35-.53.88-.8 1.57-.8.6 0 1.08.21 1.45.63.36.41.54.97.54 1.67z"/>'
  };

  function buildSprite() {
    var parts = ['<svg xmlns="http://www.w3.org/2000/svg" style="display:none" aria-hidden="true">'];
    Object.keys(UI_ICONS).forEach(function (k) {
      parts.push('<symbol id="i-' + k + '" viewBox="0 0 16 16">' + UI_ICONS[k] + '</symbol>');
    });
    Object.keys(PLATFORM_ICONS).forEach(function (k) {
      parts.push('<symbol id="p-' + k + '" viewBox="0 0 16 16" fill="currentColor" stroke="none">' + PLATFORM_ICONS[k] + '</symbol>');
    });
    parts.push('</svg>');
    var host = document.createElement('div');
    host.innerHTML = parts.join('');
    document.body.insertBefore(host.firstChild, document.body.firstChild);
  }

  /* --- Theme -------------------------------------------------------------- */

  var STORE = 'mera-theme';

  function applyTheme(t) {
    document.documentElement.setAttribute('data-theme', t);
    try { localStorage.setItem(STORE, t); } catch (e) {}
    document.querySelectorAll('[data-theme-toggle]').forEach(function (b) {
      var isDark = t === 'dark';
      b.setAttribute('aria-pressed', String(isDark));
      b.setAttribute('aria-label', isDark ? 'Switch to light theme' : 'Switch to dark theme');
      var use = b.querySelector('use');
      if (use) use.setAttribute('href', isDark ? '#i-sun' : '#i-moon');
    });
  }

  function initTheme() {
    var saved = null;
    try { saved = localStorage.getItem(STORE); } catch (e) {}
    var prefers = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    applyTheme(saved || (prefers ? 'dark' : 'light'));
  }

  /* --- Command palette ---------------------------------------------------- */

  var SCREENS = [
    ['Dashboard', 'dashboard.html', 'Queue, upcoming, agent activity'],
    ['Approval Queue', 'approvals.html', 'Per-account decisions'],
    ['Composer', 'composer.html', 'Target picker and per-account previews'],
    ['Content Calendar', 'calendar.html', 'Colour-coded by group'],
    ['Unified Inbox', 'inbox.html', 'Comments across accounts'],
    ['Analytics', 'analytics.html', 'Account, group and workspace'],
    ['Connected Accounts', 'accounts.html', 'Health and reconnection'],
    ['Account Groups', 'groups.html', 'Membership and staggering'],
    ['Sources', 'sources.html', 'Feeds and group links'],
    ['Content Discovery', 'discovery.html', 'Trending, scored per group'],
    ['Agents', 'agents.html', 'Configuration and run viewer'],
    ['Brand Voice', 'brand-voice.html', 'Three scopes'],
    ['Automation', 'automation.html', 'Autopilot and kill switch'],
    ['Approval Channels', 'approval-channels.html', 'Telegram and WhatsApp'],
    ['MCP Integrations', 'mcp.html', 'External servers and allowlists'],
    ['Team & Roles', 'team.html', 'Members and scopes'],
    ['Audit Log', 'audit.html', 'Every action, every actor'],
    ['Settings', 'settings.html', 'Workspace and usage']
  ];

  function buildPalette() {
    var el = document.createElement('div');
    el.id = 'cmdpalette';
    el.hidden = true;
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-modal', 'true');
    el.setAttribute('aria-label', 'Command palette');
    el.innerHTML =
      '<div class="cmdp__scrim" data-close></div>' +
      '<div class="cmdp__box">' +
        '<div class="cmdp__input">' +
          '<svg class="icon" aria-hidden="true"><use href="#i-search"/></svg>' +
          '<input type="text" placeholder="Go to screen\u2026" aria-label="Search screens" autocomplete="off">' +
          '<kbd>Esc</kbd>' +
        '</div>' +
        '<ul class="cmdp__list" role="listbox"></ul>' +
      '</div>';
    document.body.appendChild(el);

    var style = document.createElement('style');
    style.textContent =
      '#cmdpalette{position:fixed;inset:0;z-index:200;display:grid;place-items:start center;padding-top:14vh}' +
      '#cmdpalette[hidden]{display:none}' +
      '.cmdp__scrim{position:absolute;inset:0;background:rgba(10,10,9,.42)}' +
      '.cmdp__box{position:relative;width:min(540px,92vw);background:var(--bg-raised);border:1px solid var(--border-strong);border-radius:var(--r-lg);box-shadow:var(--shadow-popover);overflow:hidden}' +
      '.cmdp__input{display:flex;align-items:center;gap:9px;padding:11px 13px;border-bottom:1px solid var(--border)}' +
      '.cmdp__input input{flex:1;font-size:14.5px;outline:none}' +
      '.cmdp__input .icon{color:var(--fg-subtle)}' +
      '.cmdp__list{max-height:min(380px,54vh);overflow-y:auto;padding:5px}' +
      '.cmdp__item{display:grid;gap:1px;padding:7px 9px;border-radius:var(--r-md);cursor:pointer}' +
      '.cmdp__item[aria-selected="true"]{background:var(--bg-active)}' +
      '.cmdp__item strong{font-size:13.5px;font-weight:550}' +
      '.cmdp__item span{font-size:12.5px;color:var(--fg-subtle)}' +
      '.cmdp__none{padding:22px;text-align:center;font-size:13px;color:var(--fg-muted)}';
    document.head.appendChild(style);

    var input = el.querySelector('input');
    var list = el.querySelector('.cmdp__list');
    var idx = 0;
    var shown = SCREENS.slice();

    function render() {
      if (!shown.length) {
        list.innerHTML = '<li class="cmdp__none">No screen matches that.</li>';
        return;
      }
      list.innerHTML = shown.map(function (s, i) {
        return '<li class="cmdp__item" role="option" aria-selected="' + (i === idx) + '" data-href="' + s[1] + '">' +
          '<strong>' + s[0] + '</strong><span>' + s[2] + '</span></li>';
      }).join('');
    }

    function filter() {
      var q = input.value.trim().toLowerCase();
      shown = q ? SCREENS.filter(function (s) {
        return (s[0] + ' ' + s[2]).toLowerCase().indexOf(q) > -1;
      }) : SCREENS.slice();
      idx = 0;
      render();
    }

    function open() {
      el.hidden = false;
      input.value = '';
      filter();
      input.focus();
    }
    function close() { el.hidden = true; }

    input.addEventListener('input', filter);
    list.addEventListener('click', function (e) {
      var item = e.target.closest('.cmdp__item');
      if (item) window.location.href = item.dataset.href;
    });
    el.querySelector('[data-close]').addEventListener('click', close);

    el.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { close(); return; }
      if (e.key === 'ArrowDown') { e.preventDefault(); idx = Math.min(idx + 1, shown.length - 1); render(); }
      if (e.key === 'ArrowUp') { e.preventDefault(); idx = Math.max(idx - 1, 0); render(); }
      if (e.key === 'Enter' && shown[idx]) { window.location.href = shown[idx][1]; }
    });

    document.addEventListener('keydown', function (e) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); open(); }
    });
    document.querySelectorAll('[data-cmdk]').forEach(function (b) {
      b.addEventListener('click', open);
    });
  }

  /* --- Shell -------------------------------------------------------------
     The rail is identical on every screen, so it is rendered from one config
     rather than copied into eighteen files where it would drift.
     Pages opt in with <aside class="rail" data-nav="approvals"></aside>.
     --------------------------------------------------------------------- */

  var NAV = [
    ['Work', [
      ['dashboard',  'Dashboard',  'dashboard.html',  'dashboard', ''],
      ['approvals',  'Approvals',  'approvals.html',  'approvals', '7'],
      ['composer',   'Composer',   'composer.html',   'compose',   ''],
      ['calendar',   'Calendar',   'calendar.html',   'calendar',  ''],
      ['inbox',      'Inbox',      'inbox.html',      'inbox',     '12'],
      ['analytics',  'Analytics',  'analytics.html',  'analytics', '']
    ]],
    ['Content', [
      ['sources',    'Sources',    'sources.html',    'sources',   ''],
      ['discovery',  'Discovery',  'discovery.html',  'discovery', ''],
      ['voice',      'Brand Voice','brand-voice.html','voice',     '']
    ]],
    ['Accounts', [
      ['accounts',   'Connected',  'accounts.html',   'accounts',  '9'],
      ['groups',     'Groups',     'groups.html',     'groups',    '']
    ]],
    ['System', [
      ['agents',     'Agents',     'agents.html',     'agents',    ''],
      ['automation', 'Automation', 'automation.html', 'automation',''],
      ['channels',   'Channels',   'approval-channels.html', 'channels', ''],
      ['mcp',        'MCP',        'mcp.html',        'mcp',       ''],
      ['team',       'Team',       'team.html',       'team',      ''],
      ['audit',      'Audit Log',  'audit.html',      'audit',     ''],
      ['settings',   'Settings',   'settings.html',   'settings',  '']
    ]]
  ];

  function renderRail(rail) {
    var current = rail.dataset.nav || '';
    var groups = NAV.map(function (g) {
      var items = g[1].map(function (it) {
        var isHere = it[0] === current;
        return '<a class="navitem" href="' + it[2] + '"' + (isHere ? ' aria-current="page"' : '') + '>' +
          '<svg class="icon" aria-hidden="true"><use href="#i-' + it[3] + '"/></svg>' + it[1] +
          (it[4] ? '<span class="navitem__count">' + it[4] + '</span>' : '') + '</a>';
      }).join('');
      return '<div class="rail__group"><div class="rail__label">' + g[0] + '</div>' + items + '</div>';
    }).join('');

    rail.innerHTML =
      '<div class="rail__head">' +
        '<button class="ws" aria-haspopup="listbox">' +
          '<span class="ws__mark">AC</span>' +
          '<span class="ws__text"><span class="ws__name truncate">Acme</span>' +
          '<span class="ws__meta">9 accounts · 3 groups</span></span>' +
          '<svg class="icon icon--sm subtle" aria-hidden="true"><use href="#i-chevdown"/></svg>' +
        '</button>' +
      '</div>' +
      '<nav class="rail__nav" aria-label="Main">' + groups + '</nav>' +
      '<div class="rail__foot">' +
        '<button class="killswitch"><svg class="icon" aria-hidden="true"><use href="#i-power"/></svg>Stop all automation</button>' +
      '</div>';
  }

  function renderTopbar(bar) {
    var title = bar.dataset.title || '';
    var sub = bar.dataset.sub || '';
    var action = bar.dataset.action || '';
    var href = bar.dataset.actionHref || 'composer.html';

    bar.innerHTML =
      '<button class="btn btn--icon btn--ghost railtoggle" data-rail-toggle aria-label="Open navigation">' +
        '<svg class="icon" aria-hidden="true"><use href="#i-menu"/></svg></button>' +
      '<div class="topbar__title"><h1>' + title + '</h1>' +
        (sub ? '<span class="small subtle">' + sub + '</span>' : '') + '</div>' +
      '<div class="topbar__spacer"></div>' +
      '<button class="cmdk" data-cmdk><svg class="icon icon--sm" aria-hidden="true"><use href="#i-search"/></svg>' +
        '<span class="cmdk__label">Search</span><kbd>⌘</kbd><kbd>K</kbd></button>' +
      '<button class="btn btn--icon" data-theme-toggle aria-pressed="false" aria-label="Switch to dark theme">' +
        '<svg class="icon" aria-hidden="true"><use href="#i-moon"/></svg></button>' +
      (action ? '<a class="btn btn--primary" href="' + href + '">' +
        '<svg class="icon icon--sm" aria-hidden="true"><use href="#i-plus"/></svg><span class="btn__label">' + action + '</span></a>' : '');
  }

  function initShell() {
    document.querySelectorAll('.rail[data-nav]').forEach(function (r) {
      if (!r.children.length) renderRail(r);
    });
    document.querySelectorAll('.topbar[data-title]').forEach(function (b) {
      if (!b.children.length) renderTopbar(b);
    });
    // Wide tables scroll inside their panel, never the whole page.
    document.querySelectorAll('table.table').forEach(function (t) {
      if (t.parentElement && t.parentElement.classList.contains('tablewrap')) return;
      var w = document.createElement('div');
      w.className = 'tablewrap';
      t.parentNode.insertBefore(w, t);
      w.appendChild(t);
    });
  }

  /* --- Small interactions ------------------------------------------------- */

  function initInteractions() {
    // Mobile rail
    document.querySelectorAll('[data-rail-toggle]').forEach(function (b) {
      b.addEventListener('click', function () {
        var rail = document.querySelector('.rail');
        if (!rail) return;
        rail.dataset.open = rail.dataset.open === 'true' ? 'false' : 'true';
      });
    });

    // Theme
    document.querySelectorAll('[data-theme-toggle]').forEach(function (b) {
      b.addEventListener('click', function () {
        var cur = document.documentElement.getAttribute('data-theme');
        applyTheme(cur === 'dark' ? 'light' : 'dark');
      });
    });

    // Collapse / expand a post set's variants
    document.querySelectorAll('[data-toggle-variants]').forEach(function (b) {
      b.addEventListener('click', function () {
        var set = b.closest('.set');
        if (!set) return;
        var v = set.querySelector('.variants');
        if (!v) return;
        var open = v.hidden;
        v.hidden = !open;
        b.setAttribute('aria-expanded', String(open));
        var use = b.querySelector('use');
        if (use) use.setAttribute('href', open ? '#i-chevdown' : '#i-chevright');
      });
    });

    // Toggle buttons: switches, chips, segmented controls
    document.querySelectorAll('.switch').forEach(function (s) {
      s.addEventListener('click', function () {
        s.setAttribute('aria-checked', s.getAttribute('aria-checked') === 'true' ? 'false' : 'true');
      });
    });
    document.querySelectorAll('.chip[aria-pressed]').forEach(function (c) {
      c.addEventListener('click', function () {
        c.setAttribute('aria-pressed', c.getAttribute('aria-pressed') === 'true' ? 'false' : 'true');
      });
    });
    document.querySelectorAll('.seg').forEach(function (seg) {
      seg.addEventListener('click', function (e) {
        var b = e.target.closest('button');
        if (!b) return;
        seg.querySelectorAll('button').forEach(function (x) { x.setAttribute('aria-pressed', 'false'); });
        b.setAttribute('aria-pressed', 'true');
      });
    });

    // Calendar: every item opens a reschedule dialog, so moving a post never
    // depends on drag-and-drop. Nothing moves until "Move" is pressed.
    var evs = document.querySelectorAll('.cal__ev');
    if (evs.length) {
      var dlg = document.createElement('div');
      dlg.className = 'resched';
      dlg.hidden = true;
      dlg.setAttribute('role', 'dialog');
      dlg.setAttribute('aria-modal', 'false');
      dlg.setAttribute('aria-labelledby', 'resched-title');
      dlg.innerHTML =
        '<div class="resched__head"><strong id="resched-title">Reschedule</strong>' +
          '<span class="xs subtle" data-r-what></span></div>' +
        '<div class="resched__body">' +
          '<div class="field"><label for="r-day">Day</label><select class="select" id="r-day">' +
            ['Mon 10', 'Tue 11', 'Wed 12', 'Thu 13', 'Fri 14', 'Sat 15', 'Sun 16'].map(function (d) {
              return '<option>' + d + ' Nov</option>'; }).join('') + '</select></div>' +
          '<div class="field"><label for="r-time">Time</label><input class="input" id="r-time" type="time" step="300"></div>' +
          '<p class="caption">Moving keeps the approval: only a content change invalidates it. Group stagger offsets move with it.</p>' +
        '</div>' +
        '<div class="resched__foot"><button class="btn btn--sm" data-r-cancel>Cancel</button>' +
          '<span class="topbar__spacer"></span><button class="btn btn--sm btn--primary" data-r-move>Move</button></div>';
      document.body.appendChild(dlg);
      var st = document.createElement('style');
      st.textContent =
        '.resched{position:fixed;z-index:150;width:280px;background:var(--bg-raised);border:1px solid var(--border-strong);border-radius:var(--r-lg);box-shadow:var(--shadow-popover)}' +
        '.resched[hidden]{display:none}' +
        '.resched__head{display:grid;gap:2px;padding:10px 12px;border-bottom:1px solid var(--border)}' +
        '.resched__body{display:grid;gap:10px;padding:12px}' +
        '.resched__foot{display:flex;align-items:center;gap:8px;padding:10px 12px;border-top:1px solid var(--border)}';
      document.head.appendChild(st);
      var current = null;
      var closeDlg = function () { dlg.hidden = true; if (current) current.focus(); current = null; };
      evs.forEach(function (ev) {
        ev.setAttribute('aria-haspopup', 'dialog');
        ev.addEventListener('click', function () {
          current = ev;
          var label = ev.querySelector('.truncate');
          var time = ev.querySelector('.mono');
          dlg.querySelector('[data-r-what]').textContent = (label ? label.textContent : '') + (time ? ' · ' + time.textContent : '');
          dlg.querySelector('#r-time').value = time ? time.textContent.trim() : '09:00';
          var b = ev.getBoundingClientRect();
          dlg.hidden = false;
          var left = Math.min(Math.max(8, b.left), window.innerWidth - 288);
          var top = b.bottom + 6 + 260 > window.innerHeight ? Math.max(8, b.top - 266) : b.bottom + 6;
          dlg.style.left = left + 'px';
          dlg.style.top = top + 'px';
          dlg.querySelector('#r-day').focus();
        });
      });
      dlg.querySelector('[data-r-cancel]').addEventListener('click', closeDlg);
      dlg.querySelector('[data-r-move]').addEventListener('click', function () {
        var t = dlg.querySelector('#r-time').value;
        var time = current && current.querySelector('.mono');
        if (time && t) time.textContent = t;
        closeDlg();
      });
      dlg.addEventListener('keydown', function (e) { if (e.key === 'Escape') { e.preventDefault(); closeDlg(); } });
      document.addEventListener('click', function (e) {
        if (!dlg.hidden && !dlg.contains(e.target) && !(current && current.contains(e.target))) closeDlg();
      });
    }

    // Composer: live character count against the platform limit
    document.querySelectorAll('[data-counter]').forEach(function (ta) {
      var out = document.getElementById(ta.dataset.counter);
      if (!out) return;
      var limit = parseInt(out.dataset.limit, 10);
      var update = function () {
        var n = ta.value.length;
        out.textContent = n + ' / ' + limit;
        out.classList.toggle('counter--over', n > limit);
      };
      ta.addEventListener('input', update);
      update();
    });
  }

  /* --- Boot --------------------------------------------------------------- */

  function boot() {
    buildSprite();
    initShell();
    initTheme();
    buildPalette();
    initInteractions();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  // Apply theme before paint to avoid a flash.
  try {
    var pre = localStorage.getItem(STORE);
    var dark = pre ? pre === 'dark' : (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
    document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
  } catch (e) {}
})();
