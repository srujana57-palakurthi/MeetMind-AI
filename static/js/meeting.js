/* =========================================================================
   meeting.js - behaviour of the meeting results page
   (edit title, edit action items, edit the follow-up email)
   ========================================================================= */
(function () {
  'use strict';

  const cfg = window.MEETING || {};
  const $  = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  const itemsBody   = $('#itemsBody');
  const itemsEmpty  = $('#itemsEmpty');
  const saveItemsBtn = $('#saveItemsBtn');
  const itemsDirty  = $('#itemsDirty');
  const titleInput  = $('#meetingTitle');
  const saveTitleBtn = $('#saveTitleBtn');
  const emailSubject = $('#emailSubject');
  const emailBody    = $('#emailBody');
  const saveEmailBtn = $('#saveEmailBtn');
  const emailDirty   = $('#emailDirty');

  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  const dirty = { items: false, title: false, email: false };
  let anyDirty = false;

  /* ------------------------------------------------------------------ utils */
  function setDirty(section, flag) {
    dirty[section] = flag;
    anyDirty = dirty.items || dirty.title || dirty.email;

    if (section === 'items') {
      saveItemsBtn.disabled = !flag;
      itemsDirty.classList.toggle('d-none', !flag);
    }
    if (section === 'title') {
      saveTitleBtn.disabled = !flag;
    }
    if (section === 'email') {
      saveEmailBtn.disabled = !flag;
      emailDirty.classList.toggle('d-none', !flag);
    }
    updateUnloadWarning();
  }

  function updateUnloadWarning() {
    window.onbeforeunload = anyDirty
      ? () => 'You have unsaved changes. Leave this page?'
      : null;
  }

  function formatDate(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || '');
    if (!m) return iso || '';
    return m[3] + ' ' + MONTHS[parseInt(m[2], 10) - 1] + ' ' + m[1];
  }

  /** Same markup as the `deadline_meta` macro in meeting.html. */
  function deadlineMetaHTML(item) {
    if (item.deadline_type === 'absolute') {
      return '<span class="badge text-bg-light border">absolute date</span>';
    }
    if (item.deadline_type === 'relative') {
      let html = '<span class="badge text-bg-info-subtle text-info-emphasis border">relative date</span>';
      if (item.deadline_resolved) {
        html += '<span class="text-primary ms-1">&rarr; <span class="resolved-date" data-iso="' +
                item.deadline_resolved + '">' + formatDate(item.deadline_resolved) +
                '</span></span>';
      }
      return html;
    }
    if (item.deadline_type === 'unclassified') {
      return '<span class="badge text-bg-light border text-secondary">as written</span>';
    }
    return '';
  }

  function updateEmptyState() {
    if (itemsEmpty) {
      itemsEmpty.classList.toggle('d-none', $$('.item-row', itemsBody).length > 0);
    }
  }

  function renumberRows() {
    $$('.item-row', itemsBody).forEach((row, index) => {
      const num = $('.row-num', row);
      if (num) num.textContent = String(index + 1);
    });
  }

  /* ----------------------------------------------------------- action items */
  function collectItems() {
    return $$('.item-row', itemsBody).map((row) => {
      const id = row.dataset.id;
      return {
        id: id ? parseInt(id, 10) : null,
        task: $('.i-task', row).value.trim(),
        owner: $('.i-owner', row).value.trim() || null,
        deadline: $('.i-deadline', row).value.trim() || null,
        status: $('.i-status', row).value
      };
    });
  }

  function applySaved(saved) {
    const rows = $$('.item-row', itemsBody);
    saved.forEach((item, index) => {
      const row = rows[index];
      if (!row) return;
      row.dataset.id = String(item.id);
      const meta = $('.deadline-meta', row);
      if (meta) meta.innerHTML = deadlineMetaHTML(item);
      // keep the "Not specified" placeholders honest
      const owner = $('.i-owner', row);
      if (owner && !item.owner) owner.value = '';
    });
    renumberRows();
  }

  async function saveItems() {
    const items = collectItems();
    const missing = items.find((i) => !i.task);
    if (missing) {
      showToast('Every action item needs a task description.');
      return;
    }
    const original = saveItemsBtn.innerHTML;
    saveItemsBtn.disabled = true;
    saveItemsBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Saving';
    try {
      const data = await postJSON('/meeting/' + cfg.id + '/items', { items: items });
      applySaved(data.items || []);
      setDirty('items', false);
      showToast('Action items saved.');
    } catch (err) {
      saveItemsBtn.disabled = false;
      showToast(err.message);
    } finally {
      saveItemsBtn.innerHTML = original;
    }
  }

  function addItemRow() {
    const tr = document.createElement('tr');
    tr.className = 'item-row';
    tr.innerHTML =
      '<td class="ps-3 text-secondary small row-num"></td>' +
      '<td><input class="form-control form-control-sm i-task" maxlength="500" placeholder="What needs to be done?"></td>' +
      '<td><input class="form-control form-control-sm i-owner" maxlength="120" placeholder="Not specified"></td>' +
      '<td><input class="form-control form-control-sm i-deadline" maxlength="120" placeholder="Not specified">' +
      '    <div class="deadline-meta small mt-1"></div></td>' +
      '<td><select class="form-select form-select-sm i-status">' +
        cfg.statuses.map((s) => '<option value="' + s + '">' + s + '</option>').join('') +
      '</select></td>' +
      '<td class="text-end pe-3">' +
      '  <button class="btn btn-sm btn-outline-danger remove-row" type="button" title="Remove task">' +
      '    <i class="bi bi-x-lg"></i></button></td>';
    itemsBody.appendChild(tr);
    updateEmptyState();
    renumberRows();
    setDirty('items', true);
    $('.i-task', tr).focus();
  }

  /* -------------------------------------------------------------- email */
  function emailText() {
    return 'Subject: ' + emailSubject.value.trim() + '\n\n' + emailBody.value;
  }

  /* ------------------------------------------------------------ wiring */
  if (itemsBody) {
    itemsBody.addEventListener('input', () => setDirty('items', true));
    itemsBody.addEventListener('change', () => setDirty('items', true));
    itemsBody.addEventListener('click', (event) => {
      const remove = event.target.closest('.remove-row');
      if (remove) {
        remove.closest('.item-row').remove();
        updateEmptyState();
        renumberRows();
        setDirty('items', true);
      }
    });
    saveItemsBtn.addEventListener('click', saveItems);
    const addBtn = $('#addItemBtn');
    if (addBtn) addBtn.addEventListener('click', addItemRow);
    updateEmptyState();
  }

  if (titleInput && saveTitleBtn) {
    titleInput.addEventListener('input', () => setDirty('title', true));
    titleInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') { event.preventDefault(); saveTitleBtn.click(); }
    });
    saveTitleBtn.addEventListener('click', async () => {
      const original = saveTitleBtn.innerHTML;
      saveTitleBtn.disabled = true;
      saveTitleBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
      try {
        const data = await postJSON('/meeting/' + cfg.id + '/title',
                                    { title: titleInput.value });
        titleInput.value = data.title;
        setDirty('title', false);
        showToast('Title saved.');
      } catch (err) {
        showToast(err.message);
      } finally {
        saveTitleBtn.innerHTML = original;
        saveTitleBtn.disabled = dirty.title;   // keep the saved state
      }
    });
  }

  if (emailBody && saveEmailBtn) {
    const onEmailInput = () => setDirty('email', true);
    emailSubject.addEventListener('input', onEmailInput);
    emailBody.addEventListener('input', onEmailInput);

    saveEmailBtn.addEventListener('click', async () => {
      const original = saveEmailBtn.innerHTML;
      saveEmailBtn.disabled = true;
      saveEmailBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Saving';
      try {
        await postJSON('/meeting/' + cfg.id + '/email', {
          subject: emailSubject.value,
          body: emailBody.value
        });
        setDirty('email', false);
        showToast('Follow-up email saved.');
      } catch (err) {
        saveEmailBtn.disabled = false;
        showToast(err.message);
      } finally {
        saveEmailBtn.innerHTML = original;
      }
    });

    $('#copyEmailBtn').addEventListener('click', async () => {
      try {
        await copyText(emailText());
        showToast('Email copied to the clipboard.');
      } catch (err) {
        showToast('Could not copy: ' + err.message);
      }
    });

    $('#downloadEmailBtn').addEventListener('click', () => {
      downloadText('follow-up-email-' + cfg.id + '.txt', emailText());
      showToast('Email downloaded as a .txt file.');
    });
  }
})();
