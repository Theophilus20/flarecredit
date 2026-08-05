(function () {
  var KEY = 'fc:theme';
  function paint(t) {
    document.documentElement.dataset.theme = t;
    try { localStorage.setItem(KEY, t); } catch (e) {}
    document.querySelectorAll('[data-theme-toggle]').forEach(function (b) {
      b.innerHTML = '<svg><use href="#i-' + (t === 'dark' ? 'sun' : 'moon') + '"/></svg>';
    });
  }
  var saved = 'light';
  try { saved = localStorage.getItem(KEY) || 'light'; } catch (e) {}
  document.documentElement.dataset.theme = saved;
  document.addEventListener('DOMContentLoaded', function () {
    paint(saved);
    document.querySelectorAll('[data-theme-toggle]').forEach(function (b) {
      b.addEventListener('click', function () {
        paint(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
      });
    });
    // active section highlight for docs
    var links = document.querySelectorAll('.doc-side a[href^="#"], .doc-toc a[href^="#"]');
    if (!links.length) return;
    var heads = [].slice.call(document.querySelectorAll('.doc-main h2[id]'));
    function sync() {
      var y = window.scrollY + 120, cur = heads[0];
      heads.forEach(function (h) { if (h.offsetTop <= y) cur = h; });
      links.forEach(function (a) {
        a.classList.toggle('on', cur && a.getAttribute('href') === '#' + cur.id);
      });
    }
    window.addEventListener('scroll', sync, { passive: true });
    sync();
  });
})();
