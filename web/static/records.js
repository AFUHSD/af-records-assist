// Records page: runs the redaction check one record at a time so the page can show progress.

(() => {
  const table = document.getElementById('records-table');
  if (!table) return;

  const chosenModels = () => [...document.querySelectorAll('input[name=check-model]:checked')].map((i) => i.value);

  async function check(recordId) {
    const response = await fetch(table.dataset.checkUrl.replace('__ID__', recordId), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ models: chosenModels() }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  async function run(ids, thenReview = false) {
    if (!chosenModels().length) {
      toast('Choose at least one model.');
      return;
    }
    const bar = document.getElementById('check-bar');
    const text = document.getElementById('check-text');
    const buttons = document.querySelectorAll('#check-all, [data-check-one]');
    document.getElementById('check-progress').hidden = false;
    buttons.forEach((b) => { b.disabled = true; });
    const problems = [];
    let total = null; // the request's running AI cost, as the server reports it after each check
    for (const [i, id] of ids.entries()) {
      const cost = total === null ? '' : ` AI cost so far: $${total.toFixed(2)}.`;
      text.textContent = `Checking ${id} (${i + 1} of ${ids.length}). Each record takes about 10 to 30 seconds.${cost}`;
      bar.style.width = `${(i / ids.length) * 100}%`;
      try {
        const result = await check(id);
        if (typeof result.total_usd === 'number') total = result.total_usd;
        if (result.errors.length) problems.push(`${id}: ${result.errors.join('; ')}`);
      } catch (error) {
        problems.push(`${id}: ${error.message}`);
      }
    }
    bar.style.width = '100%';
    if (problems.length) {
      text.textContent = `Finished with problems. ${problems.join(' ')}`;
      setTimeout(() => window.location.reload(), 4000);
    } else if (thenReview) { // the wizard moves on: straight into the review
      text.textContent = 'All checked. Starting the review…';
      setTimeout(() => { window.location.href = table.dataset.reviewUrl; }, 600);
    } else {
      text.textContent = 'Done. Reloading…';
      setTimeout(() => window.location.reload(), 600);
    }
  }

  document.getElementById('check-all')?.addEventListener('click', () => {
    const ids = [...table.querySelectorAll('tr[data-checked=no]')].map((tr) => tr.dataset.record);
    if (ids.length) run(ids, true);
  });
  table.addEventListener('click', (event) => {
    const button = event.target.closest('[data-check-one]');
    if (button) run([button.dataset.checkOne]);
  });
})();
