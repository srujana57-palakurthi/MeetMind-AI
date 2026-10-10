/* =========================================================================
   chat.js - the "Ask about this meeting" panel on the meeting page
   Uses postJSON() from main.js.
   ========================================================================= */
(function () {
  const form = document.getElementById('chatForm');
  if (!form) return;                       // chat panel not on this page

  const log      = document.getElementById('chatLog');
  const input    = document.getElementById('chatInput');
  const sendBtn  = document.getElementById('chatSendBtn');
  const clearBtn = document.getElementById('clearChatBtn');
  const chatURL  = '/meeting/' + window.MEETING.id + '/chat';

  /** Append one message bubble and scroll it into view. */
  function addBubble(role, text) {
    const wrap = document.createElement('div');
    wrap.className = 'chat-msg chat-' + role;

    const who = document.createElement('div');
    who.className = 'chat-role small text-secondary mb-1';
    who.textContent = role === 'user' ? 'You' : 'Assistant';

    const bubble = document.createElement('div');
    bubble.className = 'chat-bubble';
    bubble.textContent = text;             // textContent = no HTML injection

    wrap.appendChild(who);
    wrap.appendChild(bubble);
    log.appendChild(wrap);
    wrap.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    return bubble;
  }

  function scrollLog() {
    log.scrollTop = log.scrollHeight;
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const question = input.value.trim();
    if (!question) return;

    input.value = '';
    sendBtn.disabled = true;
    addBubble('user', question);
    const pending = addBubble('assistant', 'Thinking\u2026');
    pending.classList.add('chat-thinking');
    scrollLog();

    try {
      const data = await postJSON(chatURL, { question: question });
      pending.textContent = data.answer;
    } catch (err) {
      pending.textContent = 'Sorry - ' + err.message;
      pending.classList.add('chat-error');
    } finally {
      pending.classList.remove('chat-thinking');
      sendBtn.disabled = false;
      input.focus();
      scrollLog();
    }
  });

  if (clearBtn) {
    clearBtn.addEventListener('click', async () => {
      if (!window.confirm('Clear this conversation?')) return;
      try {
        await postJSON(chatURL + '/clear', {});
        log.innerHTML = '<div class="text-secondary small">Chat cleared. '
          + 'Ask anything about this meeting again.</div>';
      } catch (err) {
        window.alert('Could not clear the chat: ' + err.message);
      }
    });
  }

  /* example question links fill the input */
  document.querySelectorAll('.chat-example').forEach((link) => {
    link.addEventListener('click', (event) => {
      event.preventDefault();
      input.value = link.dataset.question || '';
      input.focus();
    });
  });
})();
