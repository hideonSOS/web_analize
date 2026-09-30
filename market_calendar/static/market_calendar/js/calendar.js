/* カレンダー: 日付のマスを押す → その日で「追加」、予定を押す → その内容で「編集」。フォームは1つを使い回す */
(() => {
  const modal = document.getElementById('cal-modal');
  if (!modal) return;
  const $ = (id) => document.getElementById(id);
  const todayIso = () => {
    const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
    return d.toISOString().slice(0, 10);
  };

  function open(mode, data) {
    const edit = mode === 'edit';
    $('cal-modal-title').textContent = edit ? '予定を編集' : '予定を追加';
    $('cal-form-id').value = edit ? 'edit' : 'add';
    $('cal-id').value = edit ? data.id : '';
    $('cal-date').value = data.date || todayIso();
    $('cal-title').value = data.title || '';
    $('cal-ticker').value = data.ticker || '';
    $('cal-time').value = data.time || '';
    $('cal-memo').value = data.memo || '';
    $('cal-important').checked = data.important === '1';
    const cat = data.category || 'earnings';
    document.querySelectorAll('#cal-form input[name="category"]').forEach((r) => { r.checked = r.value === cat; });
    $('cal-del-form').hidden = !edit;
    $('cal-del-id').value = edit ? data.id : '';
    modal.hidden = false;
    $('cal-title').focus();
  }
  function close() { modal.hidden = true; }

  document.querySelector('.cal-grid').addEventListener('click', (e) => {
    const ev = e.target.closest('.cal-ev');
    if (ev) { open('edit', ev.dataset); return; }
    const day = e.target.closest('.cal-day');
    if (day) open('add', { date: day.dataset.date });
  });
  $('cal-open-add').addEventListener('click', () => open('add', {}));
  $('cal-close').addEventListener('click', close);
  modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !modal.hidden) close(); });
})();
