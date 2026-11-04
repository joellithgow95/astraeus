/* Astraeus theme bootstrap.
 *
 * Load synchronously in <head>, right after tokens.css, so the stored choice
 * is applied before first paint (no flash of the other ramp):
 *
 *     <link rel="stylesheet" href="{cms_mount}/static/tokens.css">
 *     <script src="{cms_mount}/static/theme.js"></script>
 *
 * The choice is "light", "dark" or absent (follow the system), kept in
 * localStorage under "astraeus-theme". Every admin surface is served from the
 * same origin, so a choice made in the editor holds in the gateways admin,
 * the portal and the rest. A page that pins its own theme in markup
 * (astraeus-media pins light) is left alone.
 */
(function () {
  var root = document.documentElement;
  if (root.hasAttribute('data-astraeus-theme')) return;
  try {
    var choice = localStorage.getItem('astraeus-theme');
    if (choice === 'light' || choice === 'dark') {
      root.setAttribute('data-astraeus-theme', choice);
    }
  } catch (e) { /* storage blocked: follow the system */ }
})();
