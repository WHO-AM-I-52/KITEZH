/* ═══════════════════════════════════════════════════════════════
   update_banner.js  —  глобальный баннер запланированного обновления
   Подключается в base.html для всех авторизованных пользователей.
   Эндпойнты: /api/update/pre-status, /api/update/schedule/cancel,
              /api/update/apply, /api/update/status
   ═══════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  var IS_ADMIN     = (document.body.getAttribute('data-is-admin') === '1');
  var POLL_INTERVAL = 5000;   // мс — опрос pre-status
  var STATUS_INTERVAL = 3000; // мс — опрос update/status (только когда in_progress)

  var banner        = document.getElementById('updBanner');
  var bannerText    = document.getElementById('updBannerText');
  var bannerBy      = document.getElementById('updBannerBy');
  var btnCancel     = document.getElementById('updBannerCancel');
  var btnNow        = document.getElementById('updBannerNow');
  var modal         = document.getElementById('updDoneModal');
  var btnReload     = document.getElementById('updDoneReload');

  if (!banner) return;

  var countdownTimer  = null;
  var statusPoller    = null;
  var wasInProgress   = false;
  var fireAtTs        = 0;

  /* ── Обратный отсчёт ── */
  function startCountdown() {
    if (countdownTimer) return;
    countdownTimer = setInterval(function () {
      var left = Math.max(0, Math.round(fireAtTs - Date.now() / 1000));
      if (bannerText) bannerText.textContent = 'Обновление через ' + left + ' сек.';
    }, 1000);
  }

  function stopCountdown() {
    if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null; }
  }

  /* ── Показ / скрытие баннера ── */
  function showBanner(data) {
    fireAtTs = data.fire_at_ts || (Date.now() / 1000 + (data.seconds_left || 0));
    var left = Math.max(0, Math.round(fireAtTs - Date.now() / 1000));
    if (bannerText) bannerText.textContent = 'Обновление через ' + left + ' сек.';
    if (bannerBy)   bannerBy.textContent   = data.scheduled_by ? ('Инициировал: ' + data.scheduled_by) : '';
    if (btnCancel)  btnCancel.style.display = IS_ADMIN ? '' : 'none';
    if (btnNow)     btnNow.style.display    = IS_ADMIN ? '' : 'none';
    banner.style.display = 'block';
    startCountdown();
    startStatusPoller();
  }

  function hideBanner() {
    banner.style.display = 'none';
    stopCountdown();
  }

  /* ── Поллер pre-status (каждые 5 сек, все пользователи) ── */
  function pollPreStatus() {
    fetch('/api/update/pre-status', { cache: 'no-store', credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        if (data.scheduled) { showBanner(data); }
        else                 { hideBanner(); }
      })
      .catch(function () {});
  }

  setInterval(pollPreStatus, POLL_INTERVAL);
  setTimeout(pollPreStatus, 800); // первый опрос быстро после загрузки

  /* ── Кнопка «Отменить» (только admin) ── */
  if (btnCancel) {
    btnCancel.addEventListener('click', function () {
      btnCancel.disabled = true;
      fetch('/api/update/schedule/cancel', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' }
      })
        .then(function (r) { return r.json(); })
        .then(function () { hideBanner(); })
        .catch(function () {})
        .finally(function () { btnCancel.disabled = false; });
    });
  }

  /* ── Кнопка «Сейчас» (только admin) ── */
  if (btnNow) {
    btnNow.addEventListener('click', function () {
      if (!confirm('Запустить обновление немедленно?')) return;
      btnNow.disabled = true;
      fetch('/api/update/apply', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' }
      })
        .then(function (r) { return r.json(); })
        .then(function () { hideBanner(); wasInProgress = true; })
        .catch(function () {})
        .finally(function () { btnNow.disabled = false; });
    });
  }

  /* ── Поллер update/status — показывает модалку после рестарта (только admin) ── */
  function startStatusPoller() {
    if (!IS_ADMIN) return;
    if (statusPoller) return;
    statusPoller = setInterval(function () {
      fetch('/api/update/status', { cache: 'no-store', credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (!data) return;
          if (data.in_progress) {
            wasInProgress = true;
          } else if (wasInProgress) {
            clearInterval(statusPoller);
            statusPoller = null;
            wasInProgress = false;
            showDoneModal();
          }
        })
        .catch(function () {});
    }, STATUS_INTERVAL);
  }

  /* ── Модалка «Обновление завершено» ── */
  function showDoneModal() {
    if (!modal) return;
    var bsModal = new bootstrap.Modal(modal, { backdrop: 'static', keyboard: false });
    bsModal.show();
  }

  if (btnReload) {
    btnReload.addEventListener('click', function () { window.location.reload(); });
  }

})();
