(function () {
  'use strict';

  var panel = document.getElementById('historyPanel');
  var body = document.getElementById('historyBody');
  var toggleButton = document.getElementById('historyToggleButton');
  var refreshButton = document.getElementById('historyRefreshButton');
  var changesToggleButton = document.getElementById('historyChangesToggleButton');
  var staleBadge = document.getElementById('historyStaleBadge');
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
  var state = {
    expanded: false,
    stale: false,
    hasSummary: false,
    changesLoaded: false,
    comparisonKey: null,
    runId: null,
    kind: 'all',
    offset: 0,
    total: 0,
    items: []
  };
  var kinds = [
    ['all', 'Все'], ['new', 'Новые'], ['removed', 'Исчезли'],
    ['changed_source', 'Изменённые'], ['improved', 'Улучшились'],
    ['worsened', 'Ухудшились'], ['changed_inclusion', 'Смена статуса'],
    ['error', 'Ошибки']
  ];

  function show(node, visible) {
    if (node) node.classList.toggle('d-none', !visible);
  }

  function clear(node) {
    while (node && node.firstChild) node.removeChild(node.firstChild);
  }

  function setText(node, value) {
    if (node) node.textContent = value == null || value === '' ? '—' : String(value);
  }

  function score(value) {
    return value == null ? '—' : String(value) + '%';
  }

  function inclusion(value) {
    return value == null ? '—' : (value ? 'включена' : 'исключена');
  }

  function setExpanded(expanded) {
    state.expanded = expanded;
    show(body, expanded);
    if (toggleButton) {
      toggleButton.textContent = expanded ? 'Скрыть' : 'Показать';
      toggleButton.setAttribute('aria-expanded', String(expanded));
    }
  }

  function setStale(stale) {
    state.stale = stale;
    if (staleBadge) staleBadge.textContent = stale ? 'Новый запуск' : '';
    show(staleBadge, stale);
  }

  function resetVisibleState() {
    show(loading, false);
    show(empty, false);
    show(error, false);
    show(content, false);
    show(comparisonBox, false);
    show(filtersBox, false);
    show(changesBox, false);
    show(loadMoreBox, false);
    show(changesToggleButton, false);
  }

  function resetChanges() {
    state.changesLoaded = false;
    state.kind = 'all';
    state.offset = 0;
    state.total = 0;
    state.items = [];
    show(filtersBox, false);
    show(changesBox, false);
    show(loadMoreBox, false);
    clear(changesBox);
    clear(loadMoreBox);
  }

  function showError() {
    resetVisibleState();
    setText(error, 'Не удалось загрузить историю запусков.');
    show(error, true);
  }

  function appendLine(box, label, value) {
    var line = document.createElement('div');
    line.className = 'mt-1';
    var key = document.createElement('span');
    key.className = 'text-muted';
    key.textContent = label + ': ';
    var result = document.createElement('span');
    setText(result, value);
    line.appendChild(key);
    line.appendChild(result);
    box.appendChild(line);
  }

  function renderRun(box, title, run, fallback) {
    clear(box);
    var heading = document.createElement('div');
    heading.className = 'fw-semibold';
    heading.style.fontSize = '13px';
    heading.textContent = title;
    box.appendChild(heading);
    if (!run) {
      var note = document.createElement('div');
      note.className = 'text-muted mt-1';
      note.style.fontSize = '12px';
      note.textContent = fallback;
      box.appendChild(note);
      return;
    }
    var details = document.createElement('div');
    details.className = 'mt-1';
    details.style.fontSize = '12px';
    appendLine(details, 'Запуск', run.id);
    appendLine(details, 'Дата', run.created_at);
    appendLine(details, 'Файл', run.source_label);
    appendLine(details, 'Версия формулы', run.formula_version);
    appendLine(details, 'Площадок', run.total_sites);
    appendLine(details, 'Средний score', run.average_score == null ? null : run.average_score + '%');
    box.appendChild(details);
  }

  function renderCounters(comparison) {
    clear(comparisonBox);
    var wrap = document.createElement('div');
    wrap.className = 'border rounded p-3';
    var title = document.createElement('div');
    title.className = 'fw-semibold';
    title.style.fontSize = '13px';
    title.textContent = 'Изменения между запусками';
    wrap.appendChild(title);
    [
      ['Новые', comparison.new_sites], ['Исчезли', comparison.removed_sites],
      ['Изменённые', comparison.changed_source_sites], ['Улучшились', comparison.improved_sites],
      ['Ухудшились', comparison.worsened_sites], ['Смена статуса', comparison.changed_inclusion_sites],
      ['Ошибки', comparison.error_sites]
    ].forEach(function (pair) { appendLine(wrap, pair[0], pair[1] || 0); });
    comparisonBox.appendChild(wrap);
    show(comparisonBox, true);
  }

  function renderFilters() {
    clear(filtersBox);
    var label = document.createElement('span');
    label.className = 'fw-semibold me-1';
    label.style.fontSize = '13px';
    label.textContent = 'Фильтр:';
    filtersBox.appendChild(label);
    kinds.forEach(function (pair) {
      var button = document.createElement('button');
      button.type = 'button';
      button.className = 'btn btn-sm ' + (state.kind === pair[0] ? 'btn-primary' : 'btn-outline-secondary');
      button.style.fontSize = '12px';
      button.textContent = pair[1];
      button.addEventListener('click', function () {
        if (state.kind === pair[0]) return;
        state.kind = pair[0];
        state.offset = 0;
        state.items = [];
        loadChanges(false);
      });
      filtersBox.appendChild(button);
    });
    show(filtersBox, true);
  }

  function renderChange(item) {
    var card = document.createElement('div');
    card.className = 'border rounded p-2 mb-2';
    card.style.fontSize = '12px';
    var title = document.createElement('div');
    title.className = 'fw-semibold';
    title.textContent = item.site_id + (item.site_name ? ' — ' + item.site_name : '');
    card.appendChild(title);
    appendLine(card, 'Тип', item.primary_kind || '—');
    appendLine(card, 'Предыдущий score', score(item.previous_score));
    appendLine(card, 'Текущий score', item.is_removed ? 'Площадка отсутствует' : score(item.current_score));
    appendLine(card, 'Изменение score', item.score_delta == null ? null : (item.score_delta > 0 ? '+' : '') + item.score_delta + '%');
    appendLine(card, 'Предыдущее включение', inclusion(item.previous_is_included));
    appendLine(card, 'Текущее включение', item.is_removed ? '—' : inclusion(item.current_is_included));
    return card;
  }

  function renderChanges() {
    clear(changesBox);
    state.items.forEach(function (item) { changesBox.appendChild(renderChange(item)); });
    show(changesBox, true);
    clear(loadMoreBox);
    if (state.items.length < state.total) {
      var button = document.createElement('button');
      button.type = 'button';
      button.className = 'btn btn-sm btn-outline-secondary';
      button.textContent = 'Загрузить ещё';
      button.addEventListener('click', function () {
        state.offset = state.items.length;
        loadChanges(true);
      });
      loadMoreBox.appendChild(button);
      show(loadMoreBox, true);
    } else {
      show(loadMoreBox, false);
    }
  }

  function loadChanges(append) {
    var url = '/investmap/v2/history/runs/' + encodeURIComponent(state.runId) +
      '/changes?kind=' + encodeURIComponent(state.kind) + '&limit=25&offset=' + state.offset;
    return fetch(url, { credentials: 'same-origin' })
      .then(function (response) {
        if (!response.ok) throw new Error('history changes');
        return response.json();
      })
      .then(function (payload) {
        state.total = payload.total || 0;
        state.items = append ? state.items.concat(payload.items || []) : (payload.items || []);
        state.changesLoaded = true;
        renderFilters();
        renderChanges();
      })
      .catch(showError);
  }

  function comparisonKey(payload) {
    var latest = payload.latest || {};
    var previous = payload.previous || {};
    var comparison = payload.comparison || {};
    return [latest.id, previous.id, latest.formula_version, previous.formula_version, comparison.available].join('|');
  }

  function renderSummary(payload, changed) {
    resetVisibleState();
    var comparison = payload.comparison || {};
    if (comparison.reason === 'no_runs') {
      show(empty, true);
      return;
    }
    renderRun(latestBox, 'Последний запуск', payload.latest, 'Запусков пока нет.');
    renderRun(previousBox, 'Предыдущий запуск', payload.previous, 'Нет предыдущего запуска для сравнения.');
    show(content, true);
    if (comparison.reason === 'no_previous_run') return;
    if (comparison.reason === 'formula_version_mismatch') {
      var note = document.createElement('div');
      note.className = 'alert alert-warning mb-0';
      note.style.fontSize = '13px';
      note.textContent = 'Сравнение недоступно: версии формулы расчёта не совпадают.';
      clear(comparisonBox);
      comparisonBox.appendChild(note);
      show(comparisonBox, true);
      return;
    }
    if (!comparison.available || !payload.latest) {
      showError();
      return;
    }
    state.runId = payload.latest.id;
    renderCounters(comparison);
    show(changesToggleButton, true);
    if (changed) resetChanges();
    if (state.changesLoaded) {
      renderFilters();
      renderChanges();
    }
  }

  function loadSummary(force) {
    if (state.hasSummary && !state.stale && !force) {
      renderSummary(state.summary, false);
      return Promise.resolve();
    }
    resetVisibleState();
    show(loading, true);
    return fetch('/investmap/v2/history', { credentials: 'same-origin' })
      .then(function (response) {
        if (!response.ok) throw new Error('history summary');
        return response.json();
      })
      .then(function (payload) {
        var key = comparisonKey(payload);
        var changed = state.comparisonKey !== null && state.comparisonKey !== key;
        state.summary = payload;
        state.comparisonKey = key;
        state.hasSummary = true;
        setStale(false);
        if (changed) resetChanges();
        renderSummary(payload, changed);
      })
      .catch(showError);
  }

  function load() {
    setExpanded(true);
    return loadSummary(false);
  }

  function refresh() {
    if (!state.expanded) return load();
    return loadSummary(true);
  }

  function refreshAfterRun(runId) {
    if (!state.expanded) {
      setStale(true);
      return Promise.resolve();
    }
    resetChanges();
    state.hasSummary = false;
    setStale(true);
    return loadSummary(true);
  }

  function toggle() {
    if (state.expanded) {
      setExpanded(false);
      return;
    }
    load();
  }

  function toggleChanges() {
    if (!state.changesLoaded) {
      state.kind = 'all';
      state.offset = 0;
      state.items = [];
      loadChanges(false);
      return;
    }
    show(filtersBox, !filtersBox.classList.contains('d-none'));
    show(changesBox, !changesBox.classList.contains('d-none'));
  }

  window.investmapV2History = { load: load, refreshAfterRun: refreshAfterRun };
  if (toggleButton) toggleButton.addEventListener('click', toggle);
  if (refreshButton) refreshButton.addEventListener('click', refresh);
  if (changesToggleButton) changesToggleButton.addEventListener('click', toggleChanges);
}());
