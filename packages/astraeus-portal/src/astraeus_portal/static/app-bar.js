/* Astraeus app bar: one strip across the top of every admin page.
 *
 * It links home to the portal and across to the other apps, so no page is a
 * dead end and nobody has to remember a URL like /editor/shell. A page opts
 * in with one tag, served by the portal:
 *
 *     <script src="{portal}/app-bar.js" defer></script>
 *
 * The portal's address is read from the script's own src, and the app list
 * from {portal}/apps.json, so the portal stays the one place apps are listed.
 * If that request fails (signed out, portal down) the bar still shows the
 * home link.
 *
 * The bar is fixed, and it sets --astraeus-bar-h on <html> and the same top
 * margin on <body>, so a page sized to the viewport takes
 * calc(100dvh - var(--astraeus-bar-h, 0px)). Inside an iframe (the media
 * picker in the editor) it draws nothing.
 */
(function () {
  if (window.self !== window.top) return;
  var script = document.currentScript;
  if (!script || document.getElementById('astraeus-app-bar')) return;

  var src = new URL(script.src, location.href);
  var home = src.pathname.replace(/app-bar\.js$/, '') || '/';
  var HEIGHT = '40px';

  var css = [
    ':root { --astraeus-bar-h: ' + HEIGHT + '; }',
    'body { margin-top: var(--astraeus-bar-h) !important; }',
    '#astraeus-app-bar {',
    '  position: fixed; top: 0; left: 0; right: 0; z-index: 1000;',
    '  height: var(--astraeus-bar-h); box-sizing: border-box;',
    '  display: flex; align-items: center; gap: 4px; padding: 0 12px;',
    '  background: var(--bg-elevated, #1a1a1a);',
    '  border-bottom: 1px solid var(--border-subtle, var(--border-default, #333));',
    '  font: 500 13px/1 var(--font-sans, system-ui, sans-serif);',
    '  color: var(--text-secondary, #a3a3a3);',
    '}',
    '#astraeus-app-bar a {',
    '  display: inline-flex; align-items: center; gap: 6px; flex-shrink: 0;',
    '  height: 28px; padding: 0 10px; border-radius: var(--radius-md, 6px);',
    '  color: inherit; text-decoration: none; white-space: nowrap;',
    '}',
    '#astraeus-app-bar a:hover { background: var(--bg-hover, #202020); color: var(--text-primary, #fff); }',
    '#astraeus-app-bar a[aria-current="page"] {',
    '  background: var(--accent-dim, rgba(232,232,232,.12)); color: var(--text-primary, #fff);',
    '}',
    '#astraeus-app-bar a:focus-visible { outline: 2px solid var(--accent, #e8e8e8); outline-offset: 1px; }',
    '#astraeus-app-bar .astraeus-bar-home { color: var(--text-primary, #fff); font-weight: 600; margin-right: 4px; }',
    '#astraeus-app-bar .astraeus-bar-apps {',
    '  display: flex; gap: 2px; min-width: 0; overflow-x: auto; scrollbar-width: none;',
    '}',
    '#astraeus-app-bar .astraeus-bar-apps::-webkit-scrollbar { display: none; }',
    '#astraeus-app-bar .astraeus-bar-icon { font-size: 14px; line-height: 1; }',
    // On a phone the site name gives way to a house, so more apps fit.
    '#astraeus-app-bar .astraeus-bar-glyph { display: none; font-size: 16px; }',
    '@media (max-width: 600px) {',
    '  #astraeus-app-bar .astraeus-bar-label { display: none; }',
    '  #astraeus-app-bar .astraeus-bar-glyph { display: inline; }',
    '}',
  ].join('\n');

  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    for (var k in attrs) node.setAttribute(k, attrs[k]);
    if (text) node.textContent = text;
    return node;
  }

  // The first path segment names the app: /editor/shell and /editor/ are both
  // the editor, /media/admin/upload is media.
  function section(path) {
    var parts = path.split('/').filter(Boolean);
    return parts.length ? '/' + parts[0] : '/';
  }

  // The current app: one whose fragment the page is on, else the one whose
  // section it is in and that has no fragment.
  function markCurrent(links) {
    var here = section(location.pathname);
    var hashed = links.filter(function (l) {
      return l.url.hash && l.url.hash === location.hash && section(l.url.pathname) === here;
    });
    links.forEach(function (l) {
      var on = hashed.length
        ? hashed.indexOf(l) >= 0
        : here !== '/' && !l.url.hash && section(l.url.pathname) === here;
      if (on) l.link.setAttribute('aria-current', 'page');
      else l.link.removeAttribute('aria-current');
    });
  }

  var style = el('style', { id: 'astraeus-app-bar-style' });
  style.textContent = css;
  document.head.appendChild(style);

  var bar = el('nav', { id: 'astraeus-app-bar', 'aria-label': 'Apps' });
  var homeLink = el('a', { href: home, class: 'astraeus-bar-home', title: 'Home', 'aria-label': 'Home' });
  homeLink.appendChild(el('span', { class: 'astraeus-bar-glyph', 'aria-hidden': 'true' }, '\u2302'));
  var homeLabel = el('span', { class: 'astraeus-bar-label' }, 'Home');
  homeLink.appendChild(homeLabel);
  bar.appendChild(homeLink);
  var list = el('div', { class: 'astraeus-bar-apps' });
  bar.appendChild(list);

  function mount() {
    document.body.insertBefore(bar, document.body.firstChild);
  }
  if (document.body) mount();
  else document.addEventListener('DOMContentLoaded', mount);

  fetch(home.replace(/\/?$/, '/') + 'apps.json', { credentials: 'same-origin' })
    .then(function (res) { return res.ok ? res.json() : null; })
    .then(function (data) {
      if (!data) return;
      if (data.title) homeLabel.textContent = data.title;
      var links = (data.apps || []).map(function (app) {
        var link = el('a', { href: app.path, title: app.description || app.name });
        if (app.icon) link.appendChild(el('span', { class: 'astraeus-bar-icon', 'aria-hidden': 'true' }, app.icon));
        link.appendChild(document.createTextNode(app.name));
        list.appendChild(link);
        return { link: link, url: new URL(app.path, location.href) };
      });
      markCurrent(links);
      // An app can live at a fragment of another (Media at /editor/#media),
      // and a page can move between them without loading.
      window.addEventListener('hashchange', function () { markCurrent(links); });
    })
    .catch(function () { /* keep the home link */ });
})();
