(function () {
  'use strict';

  var panel = document.getElementById('historyPanel');
  var refreshButton = document.getElementById('historyRefreshButton');
  var loading = document.getElementById('historyLoading');
  var empty = document.getElementById('historyEmpty');
  var error = document.getElementById('historyError');
  var content = document.getElementById('historyContent');
  var latestBox = document.getElementById('historyLatestRun');
  var previousBox = document.getElementById('historyPreviousRun');
  var comparisonBox = document.getElementById('historyComparison');
  var filtersBox = document.getElementById('historyChangeFilters');
  var changesBox = document.getElementById('historyChanges');
  var loadMoreBox = document.getElementById('historyLoadMore');
  var state = { runId: null, kind: 'all', offset: 0, total: 0, items: [] };
  var kinds = [
    ['all', 'Все'], ['new', 'Новые'], ['removed', 'Исчезли'],
    ['changed_source', 'Изменённые'], ['improved', 'Улучшились'],
    ['worsened', 'Ухудшились'], ['changed_inclusion', 'Смена статуса'],
    ['error', 'Ошибки']
  ];

  function show(node, yes) { node.classList.toggle('d-none', !yes); }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function text(node, value) { node.textContent = value == null || value === '' ? '—' : String(value); }
  function score(value) { return value == null ? '—' : String(value) + '%'; }
  function inclusion(value) { return value == null ? '—' : (value ? 'включена' : 'исключена'); }

  function resetView() {
    show(loading, false); show(empty, false); show(error, false);
    show(content, false); show(comparisonBox, false); show(filtersBox, false);
    show(changesBox, false); show(loadMoreBox, false);
  }

  function errorState() {
    resetView(); show(panel, true); text(error, 'Не удалось загрузить историю запусков.'); show(error, true);
  }

  function appendLine(box, label, value) {
    var line = document.createElement('div'); line.className = 'mt-1';
    var key = document.createElement('span'); key.className = 'text-muted'; key.textContent = label + ': ';
    var val = document.createElement('span'); text(val, value);
    line.appendChild(key); line.appendChild(val); box.appendChild(line);
  }

  function renderRun(box, title, run, fallback) {
    clear(box);
    var heading = document.createElement('div'); heading.className = 'fw-semibold'; heading.style.fontSize = '13px'; heading.textContent = title; box.appendChild(heading);
    if (!run) { var note = document.createElement('div'); note.className = 'text-muted mt-1'; note.style.fontSize = '12px'; note.textContent = fallback; box.appendChild(note); return; }
    var body = document.createElement('div'); body.style.fontSize = '12px'; body.className = 'mt-1';
    appendLine(body, 'Запуск', run.id); appendLine(body, 'Дата', run.created_at); appendLine(body, 'Файл', run.source_label);
    appendLine(body, 'Версия формулы', run.formula_version); appendLine(body, 'Площадок', run.total_sites); appendLine(body, 'Средний score', run.average_score == null ? null : run.average_score + '%');
    box.appendChild(body);
  }

  function renderCounters(comparison) {
    clear(comparisonBox);
    var wrap = document.createElement('div'); wrap.className = 'border rounded p-3';
    var title = document.createElement('div'); title.className = 'fw-semibold'; title.style.fontSize = '13px'; title.textContent = 'Изменения между запусками'; wrap.appendChild(title);
    [['Новые', comparison.new_sites], ['Исчезли', comparison.removed_sites], ['Изменённые', comparison.changed_source_sites], ['Улучшились', comparison.improved_sites], ['Ухудшились', comparison.worsened_sites], ['Смена статуса', comparison.changed_inclusion_sites], ['Ошибки', comparison.error_sites]].forEach(function (pair) { appendLine(wrap, pair[0], pair[1] || 0); });
    comparisonBox.appendChild(wrap); show(comparisonBox, true);
  }

  function renderFilters() {
    clear(filtersBox); var label = document.createElement('span'); label.className = 'fw-semibold me-1'; label.style.fontSize = '13px'; label.textContent = 'Фильтр:'; filtersBox.appendChild(label);
    kinds.forEach(function (pair) { var button = document.createElement('button'); button.type = 'button'; button.className = 'btn btn-sm ' + (state.kind === pair[0] ? 'btn-primary' : 'btn-outline-secondary'); button.style.fontSize = '12px'; button.textContent = pair[1]; button.addEventListener('click', function () { if (state.kind !== pair[0]) { state.kind = pair[0]; state.offset = 0; state.items = []; loadChanges(false); } }); filtersBox.appendChild(button); });
    show(filtersBox, true);
  }

  function row(item) {
    var card = document.createElement('div'); card.className = 'border rounded p-2 mb-2'; card.style.fontSize = '12px';
    var title = document.createElement('div'); title.className = 'fw-semibold'; title.textContent = item.site_id + (item.site_name ? ' — ' + item.site_name : ''); card.appendChild(title);
    var kind = document.createElement('div'); kind.className = 'text-muted mt-1'; kind.textContent = 'Тип: ' + (item.primary_kind || '—'); card.appendChild(kind);
    appendLine(card, 'Предыдущий score', score(item.previous_score)); appendLine(card, 'Текущий score', item.is_removed ? 'Площадка отсутствует' : score(item.current_score)); appendLine(card, 'Изменение score', item.score_delta == null ? null : (item.score_delta > 0 ? '+' : '') + item.score_delta + '%'); appendLine(card, 'Предыдущее включение', inclusion(item.previous_is_included)); appendLine(card, 'Текущее включение', item.is_removed ? '—' : inclusion(item.current_is_included));
    return card;
  }

  function renderChanges(append) {
    clear(changesBox); state.items.forEach(function (item) { changesBox.appendChild(row(item)); }); show(changesBox, true);
    clear(loadMoreBox); if (state.items.length < state.total) { var button = document.createElement('button'); button.type = 'button'; button.className = 'btn btn-sm btn-outline-secondary'; button.textContent = 'Загрузить ещё'; button.addEventListener('click', function () { state.offset = state.items.length; loadChanges(true); }); loadMoreBox.appendChild(button); show(loadMoreBox, true); } else show(loadMoreBox, false);
  }

  function loadChanges(append) {
    var url = '/investmap/v2/history/runs/' + encodeURIComponent(state.runId) + '/changes?kind=' + encodeURIComponent(state.kind) + '&limit=25&offset=' + state.offset;
    return fetch(url, { credentials: 'same-origin' }).then(function (response) { if (!response.ok) throw new Error('history changes'); return response.json(); }).then(function (payload) { state.total = payload.total || 0; state.items = append ? state.items.concat(payload.items || []) : (payload.items || []); renderFilters(); renderChanges(append); }).catch(errorState);
  }

  function load() {
    resetView(); show(panel, true); show(loading, true);
    return fetch('/investmap/v2/history', { credentials: 'same-origin' }).then(function (response) { if (!response.ok) throw new Error('history summary'); return response.json(); }).then(function (payload) {
      resetView(); show(panel, true);
      var comparison = payload.comparison || {};
      if (comparison.reason === 'no_runs') { show(empty, true); return; }
      renderRun(latestBox, 'Последний запуск', payload.latest, 'Запусков пока нет.'); renderRun(previousBox, 'Предыдущий запуск', payload.previous, 'Нет предыдущего запуска для сравнения.'); show(content, true);
      if (comparison.reason === 'no_previous_run') return;
      if (comparison.reason === 'formula_version_mismatch') { var note = document.createElement('div'); note.className = 'alert alert-warning mb-0'; note.style.fontSize = '13px'; note.textContent = 'Сравнение недоступно: версии формулы расчёта не совпадают.'; clear(comparisonBox); comparisonBox.appendChild(note); show(comparisonBox, true); return; }
      if (!comparison.available || !payload.latest) { errorState(); return; }
      state.runId = payload.latest.id; state.kind = 'all'; state.offset = 0; state.total = 0; state.items = []; renderCounters(comparison); renderFilters(); return loadChanges(false);
    }).catch(errorState);
  }

  function refreshAfterRun(runId) { state.runId = runId || null; return load(); }
  window.investmapV2History = { load: load, refreshAfterRun: refreshAfterRun };
  if (refreshButton) refreshButton.addEventListener('click', load);
  document.addEventListener('DOMContentLoaded', load);
}());
