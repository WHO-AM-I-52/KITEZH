/* ═══════════════════════════════════════════════════════════════
   update_banner.js  —  глобальный баннер запланированного обновления
   Подключается в base.html для всех авторизованных пользователей.
   Эндпойнты: /api/update/pre-status, /api/update/schedule/cancel,
                /api/update/schedule, /api/update/status
   v1.0.1: fix — добавляет/убирает класс upd-banner-visible на body
   v2.0.0: phase-aware текст (скачивание/отсчёт/применение);
           кнопка «Сейчас» → /api/update/schedule {delay:1}
   ═══════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  var IS_ADMIN       = (document.body.getAttribute('data-is-admin') === '1');
  var POLL_INTERVAL  = 5000;   // мс — опрос pre-status
  var STATUS_INTERVAL = 3000;  // мс — опрос update/status (только in_progress)

  var banner     = document.getElementById('updBanner');
  var bannerText = document.getElementById('updBannerText');
  var bannerBy   = document.getElementById('updBannerBy');
  var btnCancel  = document.getElementById('updBannerCancel');
  var btnNow     = document.getElementById('updBannerNow');
  var modal      = document.getElementById('updDoneModal');
  var btnReload  = document.getElementById('updDoneReload');

  if (!banner) return;

  var countdownTimer = null;
  var statusPoller   = null;
  var wasInProgress  = false;
  var fireAtTs       = 0;
  var _currentPhase  = '';

  /* ── Обратный отсчёт (только в фазе scheduled) ── */
  function _tickBanner() {
    var left = Math.max(0, Math.round(fireAtTs - Date.now() / 1000));
    if (bannerText) bannerText.textContent = 'Обновление через ' + left + ' сек.';
  }

  function startCountdown() {
    if (countdownTimer) return;
    _tickBanner();
    countdownTimer = setInterval(_tickBanner, 1000);
  }

  function stopCountdown() {
    if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null; }
  }

  /* ── Текст баннера по фазе ── */
  function _applyPhaseText(phase, secondsLeft) {
    stopCountdown();
    if (phase === 'downloading') {
      if (bannerText) bannerText.textContent = 'Скачиваем архив обновления…';
    } else if (phase === 'scheduled') {
      startCountdown();
    } else if (phase === 'applying') {
      if (bannerText) bannerText.textContent = 'Применяем обновление… Сервер скоро перезапустится.';
    } else {
      // фаллбэк: обратный отсчёт по secondsLeft
      fireAtTs = Date.now() / 1000 + (secondsLeft || 0);
      startCountdown();
    }
  }

  /* ── Показ / скрытие баннера ── */
  function showBanner(data) {
    var phase = data.phase || 'scheduled';
    _currentPhase = phase;
    fireAtTs = data.fire_at_ts || (Date.now() / 1000 + (data.seconds_left || 0));

    _applyPhaseText(phase, data.seconds_left);

    if (bannerBy) bannerBy.textContent = data.scheduled_by
      ? ('Инициировал: ' + data.scheduled_by) : '';

    // Кнопка «Отменить»: не показываем при applying
    if (btnCancel) btnCancel.style.display =
      (IS_ADMIN && phase !== 'applying') ? '' : 'none';
    // Кнопка «Сейчас»: не показываем при applying или downloading
    if (btnNow) btnNow.style.display =
      (IS_ADMIN && phase === 'scheduled') ? '' : 'none';

    banner.style.display = 'block';
    document.body.classList.add('upd-banner-visible');
    startStatusPoller();
  }

  function hideBanner() {
    banner.style.display = 'none';
    document.body.classList.remove('upd-banner-visible');
    stopCountdown();
    _currentPhase = '';
  }

  /* ── Поллер pre-status (каждые 5 сек, все пользователи) ── */
  function pollPreStatus() {
    fetch('/api/update/pre-status', { cache: 'no-store', credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        if (data.scheduled) {
          // Обновляем фазу если изменилась
          if (data.phase && data.phase !== _currentPhase) {
            showBanner(data);
          } else if (!_currentPhase) {
            showBanner(data);
          }
        } else {
          hideBanner();
        }
      })
      .catch(function () {});
  }

  setInterval(pollPreStatus, POLL_INTERVAL);
  setTimeout(pollPreStatus, 800);

  /* ── Кнопка «Отменить» (only admin) ── */
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

  /* ── Кнопка «Сейчас» (only admin) — delay=1 через /schedule ── */
  if (btnNow) {
    btnNow.addEventListener('click', function () {
      if (!confirm('Запустить обновление немедленно?')) return;
      btnNow.disabled   = true;
      btnCancel.disabled = true;
      fetch('/api/update/schedule', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ delay: 1, force: false })
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          // Не скрываем баннер — pollPreStatus сам обновит фазу
          if (data.error === 'already_in_progress' || data.error === 'already_scheduled') {
            // уже бежит — просто ждём
          }
        })
        .catch(function () {})
        .finally(function () {
          btnNow.disabled    = false;
          btnCancel.disabled = false;
        });
    });
  }

  /* ── Поллер update/status — модалка после рестарта (only admin) ── */
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
            statusPoller  = null;
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
