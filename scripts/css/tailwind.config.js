/* Build-Konfiguration fuer das ausgelieferte app/static/tailwind.css.
 * Gescannt werden alle Templates und alle Frontend-Skripte, weil Klassen dort
 * auch in Template-Literalen stehen. Kein eigenes Theme: die App nutzt
 * ausschliesslich Tailwind-Standardutilities. */
const path = require('path');
const repo = path.resolve(__dirname, '..', '..');
module.exports = {
  content: [
    path.join(repo, 'app', 'templates', '**', '*.html'),
    path.join(repo, 'app', 'static', '**', '*.js'),
  ],
  theme: { extend: {} },
  plugins: [],
};
