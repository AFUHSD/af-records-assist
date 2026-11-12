// Review screen: the original and the redacted preview side by side, with every suggestion to approve or reject.
// The tools follow the usual redaction pattern (Acrobat, FOIA and e-discovery review tools): a Redact mode in which
// highlighting text redacts it, find-and-redact for a name, and marks that stay readable until the release applies them.
// Record text and model output are untrusted, so everything is built with textContent, never innerHTML.

(() => {
  const data = JSON.parse(document.getElementById('review-data').textContent);
  const text = data.text;
  let suggestions = data.suggestions;
  let selectedKey = null; // "start:end", stable across saves (ids are renumbered by the server)
  let redactMode = false;

  const $ = (id) => document.getElementById(id);
  const original = $('pane-original');
  const preview = $('pane-preview');
  const list = $('sugg-list');
  const filter = $('filter');
  const category = $('tool-category');
  const redactAll = $('redact-all');
  const modeButton = $('redact-mode');
  const undoButton = $('undo');
  const SOURCE = { pattern: 'Rules', rig: 'Rig', claude: 'Claude', reviewer: 'You' };

  // Undo: every change first records how the list looked, so any step can be taken back.
  const history = [];
  const quote = (t) => `"${t.length > 40 ? `${t.slice(0, 40)}…` : t}"`;
  function remember(label) {
    history.push({ label, before: JSON.stringify(suggestions) });
    if (history.length > 100) history.shift();
  }
  function undo() {
    const last = history.pop();
    if (!last || data.locked) return;
    suggestions = JSON.parse(last.before);
    if (!suggestions.some((s) => keyOf(s) === selectedKey)) { // the undone item is gone: keep the keyboard useful
      const next = suggestions.find((s) => s.decision === 'pending') || suggestions[0];
      selectedKey = next ? keyOf(next) : null;
    }
    refresh(true);
    toast(`Undone: ${last.label}`);
  }

  const keyOf = (s) => `${s.start}:${s.end}`;
  const el = (tag, props = {}, ...children) => {
    const node = Object.assign(document.createElement(tag), props);
    children.flat().forEach((c) => node.append(c));
    return node;
  };
  // a piece of record text that remembers where it starts, so a highlight inside it maps back to a position
  const piece = (tag, a, b, props = {}) => {
    const node = el(tag, { textContent: text.slice(a, b), ...props });
    node.dataset.s = a;
    return node;
  };

  // ------------------------------------------------------------------ the two panes
  function segments(items) {
    const cuts = new Set([0, text.length]);
    items.forEach((s) => { cuts.add(s.start); cuts.add(s.end); });
    const points = [...cuts].sort((a, b) => a - b);
    return points.slice(0, -1).map((a, i) => {
      const b = points[i + 1];
      return { a, b, over: items.filter((s) => s.start <= a && s.end >= b) };
    });
  }

  const stateOf = (over) => (over.some((s) => s.decision === 'pending') ? 'pending'
    : over.some((s) => s.decision === 'approve') ? 'approve' : 'reject');

  // Working copy: every suggestion highlighted and still readable.
  function renderOriginal() {
    original.replaceChildren(...segments(suggestions).map(({ a, b, over }) => {
      if (!over.length) return piece('span', a, b);
      const mark = piece('mark', a, b, { className: `m-${stateOf(over)}` });
      mark.dataset.keys = over.map(keyOf).join(' ');
      if (over.some((s) => keyOf(s) === selectedKey)) mark.classList.add('is-selected');
      return mark;
    }));
  }

  // The release as it stands: confirmed redactions black, undecided suggestions gray (hidden until someone decides),
  // kept text plain again.
  function renderPreview() {
    const live = suggestions.filter((s) => s.decision !== 'reject');
    const out = [];
    let last = null; // touching pieces of the same kind join into one bar
    segments(live).forEach(({ a, b, over }) => {
      const approved = over.find((s) => s.decision === 'approve');
      const kind = approved ? 'approve' : over.length ? 'pending' : null;
      if (!kind) {
        last = null;
        out.push(piece('span', a, b));
        return;
      }
      if (kind === last) {
        out.at(-1).dataset.keys += ` ${over.map(keyOf).join(' ')}`;
        return;
      }
      last = kind;
      const label = (approved || over[0]).category;
      const bar = el('span', kind === 'approve'
        ? { className: 'redaction', textContent: `[REDACTED: ${label}]` }
        : { className: 'redaction redaction-proposed', textContent: `[${label}?]`, title: 'Suggested; not decided yet' });
      bar.dataset.keys = over.map(keyOf).join(' ');
      out.push(bar);
    });
    preview.replaceChildren(...out);
  }

  // ------------------------------------------------------------------ the suggestion list
  const shown = () => suggestions.filter((s) => {
    const by = s.found_by;
    switch (filter.value) {
      case 'pending': return s.decision === 'pending';
      case 'both': return by.includes('rig') && by.includes('claude');
      case 'claude-only': return by.includes('claude') && !by.includes('rig');
      case 'rig-only': return by.includes('rig') && !by.includes('claude');
      case 'pattern': return by.includes('pattern');
      case 'reviewer': return by.includes('reviewer');
      default: return true;
    }
  });
  const remaining = () => shown().filter((s) => s.decision === 'pending');

  function decisionButton(s, decision, label) {
    const button = el('button', { type: 'button', className: `btn btn-sm ${decision === 'approve' ? 'btn-redact' : ''}`, textContent: label });
    button.setAttribute('aria-pressed', String(s.decision === decision));
    button.disabled = data.locked;
    button.addEventListener('click', (e) => { e.stopPropagation(); decide(s, s.decision === decision ? 'pending' : decision); });
    return button;
  }

  function renderList() {
    const items = shown();
    list.replaceChildren(...items.map((s) => {
      const sources = s.found_by.map((b) => el('span', { className: `chip chip-${b}`, textContent: SOURCE[b] || b }));
      const reasons = Object.entries(s.reasons || {}).filter(([, r]) => r && r !== 'pattern match' && r !== 'Added by the reviewer')
        .map(([by, r]) => el('p', { className: 'small' }, el('strong', { textContent: `${SOURCE[by] || by}: ` }), r));
      const disagree = new Set(Object.values(s.categories || {})).size > 1
        ? el('p', { className: 'small muted', textContent: `Labels differ: ${Object.entries(s.categories).map(([b, c]) => `${SOURCE[b] || b} ${c}`).join(', ')}` }) : '';
      const actions = el('div', { className: 'sugg-actions' }, decisionButton(s, 'approve', 'Redact'), decisionButton(s, 'reject', 'Keep'));
      if (s.found_by.length === 1 && s.found_by[0] === 'reviewer' && !data.locked) {
        const remove = el('button', { type: 'button', className: 'btn btn-sm btn-ghost', textContent: 'Remove' });
        remove.addEventListener('click', (e) => {
          e.stopPropagation();
          remember(`Remove ${quote(s.text)}`);
          suggestions = suggestions.filter((x) => x !== s);
          refresh(true);
        });
        actions.append(remove);
      }
      const item = el('li', { className: `sugg is-${s.decision}`, tabIndex: 0 },
        el('div', { className: 'sugg-head' },
          el('span', { className: 'chip chip-cat', textContent: s.category, title: data.exemptions[s.category] || '' }), ...sources),
        el('blockquote', { textContent: s.text.length > 160 ? `${s.text.slice(0, 160)}…` : s.text }),
        ...reasons, disagree, actions);
      item.dataset.key = keyOf(s);
      if (keyOf(s) === selectedKey) item.classList.add('is-selected');
      item.addEventListener('click', () => select(s, true));
      return item;
    }));
    if (!items.length) list.append(el('li', { className: 'muted small', textContent: 'Nothing matches this filter.' }));
  }

  function renderProgress() {
    const n = (d) => suggestions.filter((s) => s.decision === d).length;
    $('review-progress').textContent = `${n('approve') + n('reject')} of ${suggestions.length} decided · ${n('approve')} to redact · ${n('reject')} kept visible`;
    const left = remaining().length;
    redactAll.textContent = !left ? '✓ Nothing left to decide'
      : filter.value === 'all' ? `Redact all remaining (${left})` : `Redact remaining in this view (${left})`;
    redactAll.disabled = data.locked || !left;
    undoButton.disabled = data.locked || !history.length;
    undoButton.title = history.length ? `Undo: ${history.at(-1).label}` : 'Nothing to undo';

    // the wizard: say what is left here; once nothing is, the bottom bar turns green and "next" is the obvious move
    const pending = n('pending');
    const done = !pending && suggestions.length > 0 && !data.locked;
    document.querySelector('.wizard-bar').classList.toggle('is-done', done);
    $('wizard-hint').textContent = data.locked ? 'Signed off: read only'
      : done ? `✓ This email is done: ${n('approve')} redacted, ${n('reject')} kept. Press Enter for the next one.`
        : pending ? `${pending} left to decide in this email` : 'Nothing to decide in this email';
    $('wizard-next').classList.toggle('btn-primary', !pending);
  }

  function refresh(changed) {
    renderOriginal();
    renderPreview();
    renderList();
    renderProgress();
    if (changed) queueSave();
  }

  // ------------------------------------------------------------------ choosing and deciding
  function select(s, scroll) {
    selectedKey = s ? keyOf(s) : null;
    refresh(false);
    if (s && scroll) {
      original.querySelector(`mark[data-keys~="${selectedKey}"]`)?.scrollIntoView({ block: 'center', behavior: 'smooth' });
      list.querySelector(`[data-key="${selectedKey}"]`)?.scrollIntoView({ block: 'nearest' });
    }
  }

  function decide(s, decision) {
    if (data.locked || s.decision === decision) return;
    remember(`${{ approve: 'Redact', reject: 'Keep', pending: 'Undecide' }[decision]} ${quote(s.text)}`);
    s.decision = decision;
    selectedKey = keyOf(s);
    refresh(true);
  }

  [original, preview].forEach((pane) => pane.addEventListener('click', (e) => {
    const hit = e.target.closest('[data-keys]');
    if (hit && window.getSelection().isCollapsed) {
      const key = hit.dataset.keys.split(' ')[0];
      select(suggestions.find((s) => keyOf(s) === key), true);
    }
  }));
  filter.addEventListener('change', () => { renderList(); renderProgress(); });

  redactAll.addEventListener('click', async () => {
    const left = remaining();
    if (!left.length || data.locked) return;
    const sure = await confirmModal({
      title: left.length === 1 ? 'Redact the remaining suggestion?' : `Redact the ${left.length} remaining suggestions?`,
      body: `Every undecided suggestion ${filter.value === 'all' ? 'in this email' : 'shown in the list'} will be marked Redact. `
        + 'You can still switch any of them to Keep, or press Undo.',
      ok: `Redact ${left.length}`,
    });
    if (!sure) return;
    remember(`Redact all remaining (${left.length})`);
    left.forEach((s) => { s.decision = 'approve'; });
    refresh(true);
  });

  document.addEventListener('keydown', (e) => {
    if (document.querySelector('dialog[open]')) return; // a pop-up window has the keyboard
    if (e.key === 'Escape' && redactMode) {
      setMode(false);
      return;
    }
    const typing = e.target instanceof Element && e.target.closest('input, select, textarea');
    if (!typing && (e.metaKey || e.ctrlKey) && !e.shiftKey && e.key.toLowerCase() === 'z') {
      e.preventDefault();
      undo();
      return;
    }
    const finished = document.querySelector('.wizard-bar').classList.contains('is-done');
    if (!typing && e.key === 'Enter' && finished && !(e.target instanceof Element && e.target.closest('a, button'))) {
      e.preventDefault();
      leave($('wizard-next').href); // email finished: on to the next one, once the last change is saved
      return;
    }
    if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
    const items = shown();
    const i = items.findIndex((s) => keyOf(s) === selectedKey);
    const current = items[i];
    const k = e.key.toLowerCase();
    if (k === 'j' || k === 'k') {
      const next = items[Math.min(items.length - 1, Math.max(0, i + (k === 'j' ? 1 : -1)))] || items[0];
      select(next, true);
    } else if (current && (k === 'a' || k === 'r' || k === 'u')) {
      decide(current, { a: 'approve', r: 'reject', u: 'pending' }[k]);
      if (k !== 'u') select(items[Math.min(items.length - 1, i + 1)], true);
    } else {
      return;
    }
    e.preventDefault();
  });

  // ------------------------------------------------------------------ adding redactions the models missed
  // A redaction the reviewer adds is confirmed at once; one the AI already proposed at the same spot is just approved.
  function addRedaction(start, end, label) {
    const existing = suggestions.find((s) => s.start === start && s.end === end);
    if (existing) {
      existing.decision = 'approve';
      return existing;
    }
    const added = { id: `new-${Date.now()}-${start}`, start, end, category: label, text: text.slice(start, end), decision: 'approve',
      found_by: ['reviewer'], reasons: { reviewer: 'Added by the reviewer' }, categories: { reviewer: label } };
    suggestions.push(added);
    suggestions.sort((a, b) => a.start - b.start || a.end - b.end);
    return added;
  }

  function setMode(on) {
    redactMode = on && !data.locked;
    modeButton.setAttribute('aria-pressed', String(redactMode));
    document.body.classList.toggle('redact-mode', redactMode);
    $('mode-hint').hidden = !redactMode;
  }
  modeButton.addEventListener('click', () => setMode(!redactMode));

  // Where a highlight starts or ends, as a position in the record. Gray and black bars carry no position; a
  // highlight that crosses one simply covers what the bar hides too.
  function positionIn(node, offset) {
    const host = node.nodeType === Node.TEXT_NODE ? node.parentElement : node;
    if (!host || host.dataset.s === undefined) return null;
    return Number(host.dataset.s) + (node.nodeType === Node.TEXT_NODE ? offset : 0);
  }

  document.addEventListener('mouseup', () => {
    if (!redactMode) return;
    const sel = window.getSelection();
    if (sel.isCollapsed) return;
    const pane = [original, preview].find((p) => p.contains(sel.anchorNode) && p.contains(sel.focusNode));
    if (!pane) return;
    const range = sel.getRangeAt(0);
    let start = positionIn(range.startContainer, range.startOffset);
    let end = positionIn(range.endContainer, range.endOffset);
    sel.removeAllRanges();
    if (start === null || end === null) {
      toast('Start and end the highlight on words, not on a bar.');
      return;
    }
    if (start > end) [start, end] = [end, start];
    while (start < end && /\s/.test(text[start])) start += 1;
    while (end > start && /\s/.test(text[end - 1])) end -= 1;
    if (end - start < 2) return;
    remember(`Redact ${quote(text.slice(start, end))}`);
    const added = addRedaction(start, end, category.value);
    selectedKey = keyOf(added);
    refresh(true);
    toast(`Redacted as ${category.value}`);
  });

  $('add-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const input = $('add-text');
    const wanted = input.value.trim();
    if (data.locked || wanted.length < 2) return;
    const escaped = wanted.split(/\s+/).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
    const matches = [...text.matchAll(new RegExp(escaped.join('\\s+'), 'gi'))]; // a name may wrap onto the next line
    if (!matches.length) {
      toast(`"${wanted}" is not in this email.`);
      return;
    }
    remember(`Redact all matches of ${quote(wanted)}`);
    const added = matches.map((m) => addRedaction(m.index, m.index + m[0].length, category.value));
    selectedKey = keyOf(added[0]);
    input.value = '';
    refresh(true);
    toast(`Redacted ${matches.length} match${matches.length === 1 ? '' : 'es'} as ${category.value}`);
  });

  // ------------------------------------------------------------------ saving (every change, in order)
  let dirty = false; // a change the server has not been sent yet
  let failed = false; // the last save did not reach the server
  let sending = 0;
  let chain = Promise.resolve();
  let timer = null;
  const saveState = $('save-state');

  function queueSave() {
    dirty = true;
    saveState.textContent = 'Saving…';
    clearTimeout(timer);
    timer = setTimeout(flush, 300);
  }

  // a reviewer's own redaction always goes as "new", so one brought back by Undo after a Remove is added again
  // rev: the version of this record's decisions this page was built from. The server refuses a save made from an
  // older one, so a second reviewer's stale page cannot quietly undo the first reviewer's decisions.
  let rev = data.rev || 0;
  const payload = () => JSON.stringify({ rev, suggestions: suggestions.map(({ id, start, end, category: label, decision, reasons, found_by: by }) => ({
    id: by.length === 1 && by[0] === 'reviewer' ? `new-${start}-${end}` : id,
    start, end, category: label, decision, reason: reasons && reasons.reviewer })) });

  // Send whatever is waiting, now, after any save already on its way. Resolves when the server has answered.
  function flush() {
    clearTimeout(timer);
    if (dirty) {
      dirty = false;
      sending += 1;
      chain = chain.then(send);
    }
    return chain;
  }

  async function send() {
    try {
      const response = await fetch(data.saveUrl, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: payload() });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
      failed = false;
      rev = result.rev;
      if (!dirty) {
        suggestions = result.suggestions;
        refresh(false);
      }
      saveState.textContent = 'All changes saved.';
    } catch (error) {
      failed = true;
      dirty = true; // still owed to the server; the next change or the next attempt to leave sends it again
      saveState.textContent = `Not saved: ${error.message}`;
    } finally {
      sending -= 1;
    }
  }

  // Leaving the page waits for the last change. A redaction made a moment before "Continue to sign off" must be on
  // the server before sign-off reads the record, or the release goes out without it.
  async function leave(href) {
    await flush();
    if (failed) {
      toast('Your last change is not saved, so this page is staying open. Check the connection and try again.');
      return;
    }
    window.location.href = href;
  }

  document.addEventListener('click', (event) => {
    const link = event.target instanceof Element && event.target.closest('a[href]');
    if (!link || link.target === '_blank' || event.metaKey || event.ctrlKey || event.shiftKey || (!dirty && !sending)) return;
    event.preventDefault();
    leave(link.href);
  });
  window.addEventListener('pagehide', () => { // the tab is closing or the address bar was used: a last attempt
    if (dirty) fetch(data.saveUrl, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: payload(), keepalive: true });
  });

  // ------------------------------------------------------------------ keep the two panes scrolling together
  let syncing = false;
  [[original, preview], [preview, original]].forEach(([from, to]) => {
    from.addEventListener('scroll', () => {
      if (syncing) { syncing = false; return; }
      syncing = true;
      const ratio = from.scrollTop / Math.max(1, from.scrollHeight - from.clientHeight);
      to.scrollTop = ratio * (to.scrollHeight - to.clientHeight);
    });
  });

  if (data.locked) {
    saveState.textContent = 'Reopen on the Sign off page to change it.';
    [modeButton, undoButton, redactAll, ...$('add-form').elements].forEach((c) => { c.disabled = true; });
  }
  undoButton.addEventListener('click', undo);
  const first = suggestions.find((s) => s.decision === 'pending') || suggestions[0];
  select(first || null, false);
})();
