const $ = (selector) => document.querySelector(selector);
const openModal = (el) => el.classList.add('show');
const closeModal = (el) => el.classList.remove('show');
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const state = { chatId: '', sourceId: '', sourceName: '', chatTitle: '', sources: [], answerMode: 'normal', toolHistory: [] };
let addingSource = false;
let chatSearchQuery = '';
let isGenerating = false;
let historyFilter = 'all';

const newChatModal = $('#newChatModal');
const loadingModal = $('#loadingModal');
const toolModal = $('#toolModal');
const historyModal = $('#historyModal');

marked.setOptions({ breaks: true, gfm: true });

// ── Init ──────────────────────────────────────────────────────────────────

$('#newChat').onclick = () => { addingSource = false; openModal(newChatModal); };
$('#startChat').onclick = () => { addingSource = false; openModal(newChatModal); };
$('#addSource').onclick = () => { addingSource = true; openModal(newChatModal); };
$('#closeNewChat').onclick = () => closeModal(newChatModal);
$('#closeTool').onclick = () => closeModal(toolModal);
$('#closeHistory').onclick = () => closeModal(historyModal);
$('#viewHistory').onclick = openHistory;
document.querySelectorAll('.history-filter').forEach((btn) => {
  btn.onclick = () => {
    historyFilter = btn.dataset.filter;
    document.querySelectorAll('.history-filter').forEach((b) => b.classList.toggle('active', b === btn));
    renderHistoryList();
  };
});
$('#chooseFile').onclick = () => $('#fileInput').click();
$('#chooseWebsite').onclick = () => {
  $('#websiteEntry').classList.remove('hidden');
  $('#pasteEntry').classList.add('hidden');
};
$('#choosePaste').onclick = () => {
  $('#pasteEntry').classList.remove('hidden');
  $('#websiteEntry').classList.add('hidden');
};
$('#addPaste').onclick = () => {
  const text = $('#pasteText').value.trim();
  if (text) createPasteChat(text, $('#pasteTitle').value.trim());
};
$('#addWebsite').onclick = () => {
  const url = $('#websiteUrl').value.trim();
  if (url) createWebsiteChat(url);
};
$('#fileInput').onchange = async (event) => {
  const file = event.target.files[0];
  if (file) await createFileChat(file);
};
$('#chatSearch').oninput = (e) => {
  chatSearchQuery = e.target.value.trim().toLowerCase();
  renderSidebar();
};
$('#renameChat').onclick = () => {
  const title = prompt('Rename chat', state.chatTitle);
  if (title && title.trim()) {
    state.chatTitle = title.trim();
    $('#workspaceTitle').textContent = state.chatTitle;
    persistState();
  }
};
$('#exportChat').onclick = exportChat;
$('#clearChat').onclick = clearChat;
$('#themeToggle').onclick = toggleTheme;
document.querySelectorAll('.mode-chip').forEach((btn) => {
  btn.onclick = () => {
    state.answerMode = btn.dataset.mode;
    document.querySelectorAll('.mode-chip').forEach((b) => b.classList.toggle('active', b === btn));
  };
});
if (localStorage.getItem('sirius_theme') === 'dark') {
  document.documentElement.dataset.theme = 'dark';
  $('#themeToggle').querySelector('.material-symbols-outlined').textContent = 'light_mode';
}
$('#question').onkeydown = (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    $('#composer').requestSubmit();
  }
};

// Auto-resize the composer textarea as the user types
$('#question').oninput = function () {
  this.style.height = 'auto';
  this.style.height = Math.min(this.scrollHeight, 140) + 'px';
};
document.querySelectorAll('[data-tool]').forEach(
  (button) => (button.onclick = () => openTool(button.dataset.tool))
);

// ── Chat creation ─────────────────────────────────────────────────────────

async function createFileChat(file) {
  const addToExisting = addingSource;
  closeModal(newChatModal);
  openModal(loadingModal);
  setProgress(5, 'Uploading', 'Uploading your document…');
  try {
    const content = await toBase64(file);
    setProgress(15, 'Indexing', 'Building knowledge base — this may take a minute on first use…');
    const response = await fetch('/api/upload', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: file.name, content }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error);
    if (addToExisting) {
      await addSourceToChat(result.sourceId, result.name);
    } else {
      state.chatId = `chat_${Date.now()}`;
      state.sourceId = result.sourceId;
      state.sourceName = result.name;
      state.chatTitle = file.name.replace(/\.[^.]+$/, '');
      state.sources = [{ sourceId: result.sourceId, name: result.name }];
      state.toolHistory = [];
      await finishChat();
    }
  } catch (error) {
    alert(`Upload failed: ${error.message}`);
    closeModal(loadingModal);
  }
}

async function createPasteChat(text, title) {
  const addToExisting = addingSource;
  const name = (title || 'Pasted notes').replace(/[^\w\s.-]/g, '').trim() || 'Pasted notes';
  closeModal(newChatModal);
  openModal(loadingModal);
  setProgress(10, 'Saving', 'Saving your notes…');
  try {
    setProgress(20, 'Indexing', 'Building knowledge base — this may take a minute on first use…');
    const content = btoa(unescape(encodeURIComponent(text)));
    const response = await fetch('/api/upload', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: `${name}.txt`, content }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error);
    $('#pasteText').value = '';
    $('#pasteTitle').value = '';
    if (addToExisting) {
      await addSourceToChat(result.sourceId, name);
    } else {
      state.chatId = `chat_${Date.now()}`;
      state.sourceId = result.sourceId;
      state.sourceName = name;
      state.chatTitle = name;
      state.sources = [{ sourceId: result.sourceId, name }];
      state.toolHistory = [];
      await finishChat();
    }
  } catch (error) {
    alert(`Upload failed: ${error.message}`);
    closeModal(loadingModal);
  }
}

async function createWebsiteChat(url) {
  const addToExisting = addingSource;
  closeModal(newChatModal);
  openModal(loadingModal);
  setProgress(10, 'Fetching', 'Downloading the website…');
  try {
    setProgress(20, 'Indexing', 'Building knowledge base — this may take a minute on first use…');
    const response = await fetch('/api/website', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error);
    if (addToExisting) {
      await addSourceToChat(result.sourceId, result.name);
    } else {
      state.chatId = `chat_${Date.now()}`;
      state.sourceId = result.sourceId;
      state.sourceName = result.name;
      state.chatTitle = result.name;
      state.sources = [{ sourceId: result.sourceId, name: result.name }];
      state.toolHistory = [];
      await finishChat();
    }
  } catch (error) {
    alert(`Website error: ${error.message}`);
    closeModal(loadingModal);
  }
}

async function addSourceToChat(sourceId, name) {
  if (!state.sources.some((s) => s.sourceId === sourceId)) {
    state.sources.push({ sourceId, name });
  }
  state.sourceId = sourceId;
  state.sourceName = name;
  $('#sourceName').textContent = name;
  persistState();
  renderSourceChips();
  loadSourcePreview();
  await wait(300);
  closeModal(loadingModal);
}

function toBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(',')[1]);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

async function finishChat() {
  setProgress(100, 'Ready', 'Opening your chat…');
  await wait(400);
  closeModal(loadingModal);
  openWorkspace();
}

function setProgress(pct, stage, message) {
  $('#progressBar').style.width = `${pct}%`;
  $('#progressPercent').textContent = `${pct}%`;
  $('#progressStage').textContent = stage;
  $('#loadingMessage').textContent = message;
}

// ── Workspace ─────────────────────────────────────────────────────────────

function openWorkspace(savedMessages) {
  $('#home').classList.add('hidden');
  $('#workspace').classList.remove('hidden');
  $('#workspaceTitle').textContent = state.chatTitle;
  $('#sourceName').textContent = state.sourceName;
  if (!state.sources || state.sources.length === 0) {
    state.sources = state.sourceId ? [{ sourceId: state.sourceId, name: state.sourceName }] : [];
  }
  renderSourceChips();
  loadSourcePreview();
  updateCompareButton();

  const msgContainer = $('#messages');
  msgContainer.innerHTML = '';

  if (savedMessages && savedMessages.length) {
    savedMessages.forEach(({ role, html, plainText }) => {
      const el = document.createElement('div');
      el.className = role === 'user' ? 'user-message' : 'assistant-message';
      el.innerHTML = html;
      if (plainText) el.dataset.plainText = plainText;
      msgContainer.append(el);
    });
    scrollMessages();
    renderSuggestions(false);
  } else {
    addAssistant(
      "Your source is ready. Ask me anything and I will answer only from this chat's source(s)."
    );
    persistState();
    loadSuggestions();
  }

  renderSidebar();
}

function updateCompareButton() {
  const btn = $('#compareTool');
  if (btn) btn.classList.toggle('hidden', (state.sources || []).length < 2);
}

function toggleTheme() {
  const dark = document.documentElement.dataset.theme === 'dark';
  if (dark) {
    delete document.documentElement.dataset.theme;
    localStorage.removeItem('sirius_theme');
    $('#themeToggle').querySelector('.material-symbols-outlined').textContent = 'dark_mode';
  } else {
    document.documentElement.dataset.theme = 'dark';
    localStorage.setItem('sirius_theme', 'dark');
    $('#themeToggle').querySelector('.material-symbols-outlined').textContent = 'light_mode';
  }
}

function clearChat() {
  if (!state.chatId || isGenerating) return;
  if (!confirm('Clear all messages in this chat?')) return;
  $('#messages').innerHTML = '';
  addAssistant(
    "Your source is ready. Ask me anything and I will answer only from this chat's source(s)."
  );
  persistState();
  loadSuggestions();
}

async function loadSuggestions() {
  const container = $('#suggestions');
  if (!container || !state.sourceId) return;

  const userMsgs = $('#messages').querySelectorAll('.user-message');
  if (userMsgs.length > 0) {
    renderSuggestions(false);
    return;
  }

  container.classList.remove('hidden');
  container.innerHTML = '<p class="suggestions-label">Suggested questions</p><div class="suggestion-chips"><span class="suggestion-loading">Loading…</span></div>';

  try {
    const sourceIds = state.sources.map((s) => s.sourceId);
    const response = await fetch('/api/suggest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sourceIds }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error);
    renderSuggestionChips(result.questions || []);
  } catch (_e) {
    renderSuggestionChips([
      'What are the main topics in this source?',
      'Summarise the key concepts.',
      'What should I focus on for an exam?',
      'Explain the most important idea simply.',
    ]);
  }
}

function renderSuggestionChips(questions) {
  const container = $('#suggestions');
  if (!container) return;
  const chips = container.querySelector('.suggestion-chips') || container;
  chips.innerHTML = questions.map((q) =>
    `<button type="button" class="suggestion-chip">${escapeHtml(q)}</button>`
  ).join('');
  chips.querySelectorAll('.suggestion-chip').forEach((btn) => {
    btn.onclick = () => sendQuestion(btn.textContent);
  });
  container.classList.remove('hidden');
}

function renderSuggestions(show) {
  const container = $('#suggestions');
  if (!container) return;
  container.classList.toggle('hidden', !show);
}

function setGenerating(active) {
  isGenerating = active;
  const input = $('#question');
  const btn = $('#composer').querySelector('button');
  input.disabled = active;
  if (btn) btn.disabled = active;
  $('#composer').classList.toggle('busy', active);
  const status = document.querySelector('.chat-pane .online');
  if (status) status.textContent = active ? 'Thinking…' : 'Ready';
}

function loadSourcePreview(highlightText) {
  const container = $('#documentView');
  container.classList.remove('has-preview');
  container.innerHTML = `
    <div class="source-loading">
      <span class="material-symbols-outlined spinner">autorenew</span>
      <p>Loading source preview...</p>
    </div>
  `;

  if (!state.sourceId) return;

  fetch(`/api/source-content?sourceId=${encodeURIComponent(state.sourceId)}`)
    .then((res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    })
    .then((data) => {
      renderSourceContent(data, highlightText);
    })
    .catch((err) => {
      container.classList.remove('has-preview');
      container.innerHTML = `
        <div class="source-error">
          <span class="material-symbols-outlined">error</span>
          <p>Failed to load source preview: ${escapeHtml(err.message)}</p>
        </div>
      `;
    });
}

function renderSourceContent(data, highlightText) {
  const container = $('#documentView');
  container.classList.add('has-preview');
  container.innerHTML = '';

  if (data.type === 'pdf') {
    const iframe = document.createElement('iframe');
    iframe.src = data.url;
    iframe.className = 'pdf-viewer';
    iframe.title = 'Document Viewer';
    container.appendChild(iframe);
  } else if (data.type === 'website') {
    const header = document.createElement('div');
    header.className = 'website-preview-header';
    header.innerHTML = `
      <span class="material-symbols-outlined">language</span>
      <a href="${escapeHtml(data.url)}" target="_blank" class="website-link">${escapeHtml(data.url)}</a>
    `;
    const body = document.createElement('div');
    body.className = 'source-text-content';
    body.id = 'sourceTextBody';
    body.textContent = data.content;
    container.appendChild(header);
    container.appendChild(body);
  } else if (data.type === 'md') {
    const body = document.createElement('div');
    body.className = 'source-text-content markdown-output';
    body.id = 'sourceTextBody';
    body.innerHTML = marked.parse(data.content);
    container.appendChild(body);
  } else if (data.type === 'txt' || data.type === 'text') {
    const body = document.createElement('pre');
    body.className = 'source-text-content plain-text';
    body.id = 'sourceTextBody';
    body.textContent = data.content;
    container.appendChild(body);
  } else {
    container.classList.remove('has-preview');
    container.innerHTML = `
      <div class="source-error">
        <span class="material-symbols-outlined">warning</span>
        <p>${escapeHtml(data.content || 'Preview not available for this file type.')}</p>
      </div>
    `;
    return;
  }

  if (highlightText) highlightInSource(highlightText);
}

function highlightInSource(text) {
  const body = $('#sourceTextBody');
  if (!body || !text) return;
  const snippet = text.slice(0, 80).trim();
  if (!snippet) return;
  const content = body.textContent || '';
  const idx = content.toLowerCase().indexOf(snippet.toLowerCase());
  if (idx === -1) return;
  const mark = document.createElement('mark');
  mark.style.background = '#fff3a0';
  mark.style.padding = '2px 0';
  const range = document.createRange();
  const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
  let pos = 0;
  let startNode = null;
  let startOff = 0;
  let endNode = null;
  let endOff = 0;
  const endIdx = idx + snippet.length;
  while (walker.nextNode()) {
    const node = walker.currentNode;
    const len = node.textContent.length;
    if (!startNode && pos + len > idx) {
      startNode = node;
      startOff = idx - pos;
    }
    if (!endNode && pos + len >= endIdx) {
      endNode = node;
      endOff = endIdx - pos;
      break;
    }
    pos += len;
  }
  if (startNode && endNode) {
    range.setStart(startNode, startOff);
    range.setEnd(endNode, endOff);
    range.surroundContents(mark);
    mark.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
}

function renderSourceChips() {
  const container = $('#sourceChips');
  const sources = state.sources || [];
  if (sources.length === 0) {
    container.classList.add('hidden');
    container.innerHTML = '';
    return;
  }
  container.classList.remove('hidden');
  container.innerHTML = '';
  sources.forEach((source) => {
    const chip = document.createElement('button');
    chip.className = `source-chip${source.sourceId === state.sourceId ? ' active' : ''}`;
    chip.type = 'button';
    chip.title = source.name;
    const label = document.createElement('span');
    label.textContent = source.name;
    chip.appendChild(label);
    if (sources.length > 1) {
      const remove = document.createElement('span');
      remove.className = 'chip-remove material-symbols-outlined';
      remove.textContent = 'close';
      remove.title = 'Remove source';
      remove.onclick = (e) => {
        e.stopPropagation();
        removeSource(source.sourceId);
      };
      chip.appendChild(remove);
    }
    chip.onclick = () => {
      state.sourceId = source.sourceId;
      state.sourceName = source.name;
      $('#sourceName').textContent = source.name;
      renderSourceChips();
      loadSourcePreview();
    };
    container.appendChild(chip);
  });
}

function removeSource(sourceId) {
  if (state.sources.length <= 1) return;
  if (!confirm('Remove this source from the chat?')) return;
  state.sources = state.sources.filter((s) => s.sourceId !== sourceId);
  if (state.sourceId === sourceId) {
    state.sourceId = state.sources[0].sourceId;
    state.sourceName = state.sources[0].name;
    $('#sourceName').textContent = state.sourceName;
    loadSourcePreview();
  }
  renderSourceChips();
  persistState();
}

function renderSidebar() {
  const chats = loadChats();
  const listContainer = $('#chatList');
  const searchBox = $('#chatSearch')?.closest('.search');

  const filtered = chatSearchQuery
    ? chats.filter((c) =>
        c.chatTitle.toLowerCase().includes(chatSearchQuery)
        || (c.sourceName || '').toLowerCase().includes(chatSearchQuery)
        || (c.sources || []).some((s) => s.name.toLowerCase().includes(chatSearchQuery))
      )
    : chats;

  searchBox?.classList.toggle('no-results', chatSearchQuery && filtered.length === 0);

  if (filtered.length === 0) {
    listContainer.innerHTML = chatSearchQuery
      ? '<p class="empty-recent">No matching chats</p>'
      : '<p class="empty-recent">No chats yet</p>';
    return;
  }

  listContainer.innerHTML = '';
  filtered.forEach((chat) => {
    const wrapper = document.createElement('div');
    wrapper.className = `chat-item-wrapper${chat.id === state.chatId ? ' active' : ''}`;

    const btn = document.createElement('button');
    btn.className = `chat-item${chat.id === state.chatId ? ' active' : ''}`;
    btn.innerHTML = `
      <span class="material-symbols-outlined">chat_bubble</span>
      <span>${escapeHtml(chat.chatTitle)}</span>
    `;
    btn.onclick = () => selectChat(chat.id);

    const delBtn = document.createElement('button');
    delBtn.className = 'chat-delete';
    delBtn.title = 'Delete chat';
    delBtn.innerHTML = '<span class="material-symbols-outlined">delete</span>';
    delBtn.onclick = (e) => {
      e.stopPropagation();
      deleteChat(chat.id);
    };

    wrapper.appendChild(btn);
    wrapper.appendChild(delBtn);
    listContainer.appendChild(wrapper);
  });
}

function selectChat(chatId) {
  const chats = loadChats();
  const chat = chats.find((c) => c.id === chatId);
  if (!chat) return;

  state.chatId = chat.id;
  state.sourceId = chat.sourceId;
  state.sourceName = chat.sourceName;
  state.chatTitle = chat.chatTitle;
  state.sources = chat.sources && chat.sources.length ? chat.sources : [];

  localStorage.setItem('sirius_active_chat_id', chatId);
  openWorkspace(chat.messages);
}

function deleteChat(chatId) {
  if (!confirm('Are you sure you want to delete this chat?')) return;

  let chats = loadChats();
  chats = chats.filter((c) => c.id !== chatId);
  saveChats(chats);

  if (state.chatId === chatId) {
    if (chats.length > 0) {
      selectChat(chats[0].id);
    } else {
      state.chatId = '';
      state.sourceId = '';
      state.sourceName = '';
      state.chatTitle = '';
      state.sources = [];
      localStorage.removeItem('sirius_active_chat_id');
      $('#workspace').classList.add('hidden');
      $('#home').classList.remove('hidden');
      renderSidebar();
    }
  } else {
    renderSidebar();
  }
}

// ── Chat composer ─────────────────────────────────────────────────────────

async function sendQuestion(question, { regenerate = false } = {}) {
  if (!question || !state.sourceId || isGenerating) return;

  renderSuggestions(false);

  if (regenerate) {
    const msgs = $('#messages').querySelectorAll('.user-message, .assistant-message');
    const last = msgs[msgs.length - 1];
    if (last?.classList.contains('assistant-message')) last.remove();
  } else {
    addUser(question);
  }

  const pending = addAssistant('Searching your source…', true);
  setGenerating(true);

  try {
    const sourceIds = (state.sources && state.sources.length)
      ? state.sources.map((s) => s.sourceId)
      : [state.sourceId];
    const history = getChatHistory();
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question,
        sourceIds,
        history,
        mode: state.answerMode,
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Unable to answer from this source.');
    renderAssistantAnswer(pending, result.answer, result.citations, question);
    pending.classList.remove('thinking');
    persistState();
  } catch (error) {
    pending.textContent = `I could not answer from this source: ${error.message}`;
    pending.dataset.plainText = pending.textContent;
    pending.classList.remove('thinking');
  } finally {
    setGenerating(false);
  }
}

$('#composer').onsubmit = async (event) => {
  event.preventDefault();
  const input = $('#question');
  const question = input.value.trim();
  if (!question) return;
  input.value = '';
  input.style.height = 'auto';
  await sendQuestion(question);
};

function getChatHistory() {
  const turns = [];
  Array.from($('#messages').querySelectorAll('.user-message, .assistant-message'))
    .filter((el) => !el.classList.contains('thinking'))
    .forEach((el) => {
      const role = el.classList.contains('user-message') ? 'user' : 'assistant';
      const content = (el.dataset.plainText || el.textContent || '').trim();
      if (content) turns.push({ role, content });
    });
  if (turns.length && turns[turns.length - 1].role === 'user') turns.pop();
  return turns.slice(-6);
}

function addUser(text) {
  const el = document.createElement('div');
  el.className = 'user-message';
  el.textContent = text;
  el.dataset.plainText = text;
  $('#messages').append(el);
  scrollMessages();
}

function addAssistant(text, pending = false) {
  const message = document.createElement('div');
  message.className = `assistant-message${pending ? ' thinking' : ''}`;
  message.textContent = text;
  if (!pending) message.dataset.plainText = text;
  $('#messages').append(message);
  scrollMessages();
  return message;
}

function renderAssistantAnswer(el, answer, citations, question) {
  el.innerHTML = formatAnswer(answer);
  el.dataset.plainText = answer;
  if (question) el.dataset.question = question;

  if (citations && citations.length) {
    const citeDiv = document.createElement('div');
    citeDiv.className = 'citations';
    citeDiv.innerHTML = '<strong>Sources</strong>';
    citations.forEach((c) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'citation-chip';
      let pageText = '';
      if (c.page_label) {
        pageText = ` (Page ${c.page_label})`;
      } else if (c.page !== undefined && c.page !== null) {
        pageText = ` (Page ${c.page + 1})`;
      }
      btn.textContent = `[${c.index}] ${displaySourceName(c.source)}${pageText}`;
      btn.title = c.excerpt;
      btn.onclick = () => jumpToCitation(c);
      citeDiv.appendChild(btn);
    });
    el.appendChild(citeDiv);
  }

  const actions = document.createElement('div');
  actions.className = 'message-actions';
  const copyBtn = document.createElement('button');
  copyBtn.type = 'button';
  copyBtn.className = 'msg-action';
  copyBtn.textContent = 'Copy';
  copyBtn.onclick = () => copyToClipboard(answer);
  actions.appendChild(copyBtn);

  const q = question || el.dataset.question;
  if (q) {
    const regenBtn = document.createElement('button');
    regenBtn.type = 'button';
    regenBtn.className = 'msg-action';
    regenBtn.textContent = 'Regenerate';
    regenBtn.onclick = () => sendQuestion(q, { regenerate: true });
    actions.appendChild(regenBtn);
  }

  el.appendChild(actions);
}

function jumpToCitation(citation) {
  const match = state.sources.find((s) => s.sourceId === citation.source);
  if (match && state.sourceId !== citation.source) {
    state.sourceId = match.sourceId;
    state.sourceName = match.name;
    $('#sourceName').textContent = match.name;
    renderSourceChips();
    loadSourcePreview(citation.excerpt);
  } else {
    loadSourcePreview(citation.excerpt);
  }
}

function displaySourceName(sourceId) {
  const found = state.sources.find((s) => s.sourceId === sourceId);
  if (found) return found.name;
  return sourceId.replace(/^[a-f0-9]{32}_/, '');
}

function scrollMessages() {
  const el = $('#messages');
  el.scrollTop = el.scrollHeight;
}

function formatAnswer(text) {
  return marked.parse(String(text));
}

function escapeHtml(text) {
  return String(text).replace(/[&<>'"]/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[c]
  ));
}

function copyToClipboard(text) {
  navigator.clipboard.writeText(text).catch(() => {
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
  });
}

function downloadText(filename, text) {
  const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function exportChat() {
  if (!state.chatId) return;
  const lines = [`# ${state.chatTitle}`, '', `Sources: ${state.sources.map((s) => s.name).join(', ')}`, ''];
  Array.from($('#messages').querySelectorAll('.user-message, .assistant-message'))
    .filter((el) => !el.classList.contains('thinking'))
    .forEach((el) => {
      const text = el.dataset.plainText || el.textContent.trim();
      if (!text) return;
      if (el.classList.contains('user-message')) {
        lines.push(`**You:** ${text}`, '');
      } else {
        lines.push(text, '');
      }
    });
  downloadText(`${state.chatTitle.replace(/[^\w\s-]/g, '') || 'chat'}.md`, lines.join('\n'));
}

// ── Study tools ───────────────────────────────────────────────────────────

function openTool(tool) {
  const hasSources = (state.sources && state.sources.length > 0) || state.sourceId;
  if (!hasSources) {
    alert('Please open a chat with a source first.');
    return;
  }
  if (!state.sources || state.sources.length === 0) {
    state.sources = [{ sourceId: state.sourceId, name: state.sourceName }];
  }
  if (tool === 'compare' && state.sources.length < 2) {
    alert('Compare requires at least 2 sources. Add another source first.');
    return;
  }
  $('#toolEyebrow').textContent = tool.toUpperCase();
  const titles = {
    quiz: 'Create a quiz from this chat',
    flashcards: 'Create flashcards from this chat',
    summary: 'Create a summary from this chat',
    glossary: 'Build a glossary from this chat',
    compare: 'Compare sources in this chat',
  };
  $('#toolTitle').textContent = titles[tool] || `Create ${tool} from this chat`;

  const sources = state.sources;
  const hasMultipleSources = sources.length > 1;
  const sourceOptions = sources
    .map((s) => `<option value="${escapeHtml(s.sourceId)}">${escapeHtml(s.name)}</option>`)
    .join('');

  const countDefaults = { quiz: 5, flashcards: 9, glossary: 12 };
  const countLabels = { quiz: 'Number of questions', flashcards: 'Number of flashcards', glossary: 'Number of terms' };
  const countField = countDefaults[tool] ? `
    <label class="scope-option count-option">
      <span>${countLabels[tool]}</span>
      <input type="number" id="toolCount" class="scope-input count-input"
        min="3" max="25" value="${countDefaults[tool]}">
    </label>
  ` : '';

  const historyCount = (state.toolHistory || []).filter((h) => h.tool === tool).length;
  const historyLink = historyCount > 0
    ? `<button type="button" class="tool-history-link" id="toolHistoryLink">
        <span class="material-symbols-outlined">history</span>
        View ${historyCount} past ${tool === 'quiz' ? 'quiz set' : tool}${historyCount === 1 ? '' : (tool === 'quiz' ? 's' : '')}
      </button>`
    : '';

  $('#toolContent').innerHTML = `
    <p class="tool-copy">Choose what to generate the ${tool} from.</p>
    <div class="tool-scope">
      <label class="scope-option">
        <input type="radio" name="toolScope" value="all" checked>
        <span>${hasMultipleSources ? `All ${sources.length} sources in this chat` : `<strong>${escapeHtml(state.sourceName)}</strong> (the source in this chat)`}</span>
      </label>
      ${hasMultipleSources ? `
      <label class="scope-option">
        <input type="radio" name="toolScope" value="one">
        <span>A particular document</span>
      </label>
      <select id="scopeSource" class="scope-input hidden">${sourceOptions}</select>
      ` : ''}
      <label class="scope-option">
        <input type="radio" name="toolScope" value="topic">
        <span>A particular topic</span>
      </label>
      <input type="text" id="scopeTopic" class="scope-input hidden"
        placeholder="e.g. Neural networks, Chapter 3, photosynthesis…">
      ${countField}
    </div>
    <button class="primary" id="generateTool">Generate ${tool}</button>
    ${historyLink}
  `;

  const scopeSourceSelect = $('#scopeSource');
  const scopeTopicInput = $('#scopeTopic');
  const updateScopeVisibility = () => {
    const value = document.querySelector('input[name="toolScope"]:checked')?.value || 'all';
    if (scopeSourceSelect) scopeSourceSelect.classList.toggle('hidden', value !== 'one');
    scopeTopicInput.classList.toggle('hidden', value !== 'topic');
  };
  document.querySelectorAll('input[name="toolScope"]').forEach((radio) => {
    radio.onchange = updateScopeVisibility;
  });
  updateScopeVisibility();
  openModal(toolModal);
  $('#generateTool').onclick = () => runTool(tool);
  const historyLinkBtn = $('#toolHistoryLink');
  if (historyLinkBtn) {
    historyLinkBtn.onclick = () => {
      closeModal(toolModal);
      historyFilter = tool;
      document.querySelectorAll('.history-filter').forEach((b) => b.classList.toggle('active', b.dataset.filter === tool));
      renderHistoryList();
      openModal(historyModal);
    };
  }
}

function readToolCount() {
  const input = $('#toolCount');
  if (!input) return null;
  const n = parseInt(input.value, 10);
  if (!Number.isFinite(n)) return null;
  return Math.max(3, Math.min(n, 25));
}

function readToolScope() {
  const value = document.querySelector('input[name="toolScope"]:checked')?.value || 'all';
  const count = readToolCount();
  if (value === 'one') {
    const sourceId = $('#scopeSource')?.value;
    return { sourceIds: sourceId ? [sourceId] : state.sources.map((s) => s.sourceId), topic: null, count };
  }
  if (value === 'topic') {
    const topic = ($('#scopeTopic')?.value || '').trim();
    if (!topic) return null;
    return { sourceIds: state.sources.map((s) => s.sourceId), topic, count };
  }
  return { sourceIds: state.sources.map((s) => s.sourceId), topic: null, count };
}

async function runTool(tool, scope) {
  if (!scope) {
    scope = readToolScope();
    if (!scope) {
      alert('Please enter a topic to focus on.');
      return;
    }
  }
  $('#toolContent').innerHTML = `
    <div class="tool-result">
      <span class="material-symbols-outlined tool-spinner">autorenew</span>
      <p>Generating ${tool} — this may take a moment…</p>
    </div>
  `;
  try {
    const response = await fetch('/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tool, sourceIds: scope.sourceIds, topic: scope.topic, count: scope.count }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error);
    renderToolResult(tool, result.content);
    pushToolHistory(tool, result.content, scope);
  } catch (error) {
    $('#toolContent').innerHTML = `
      <p class="tool-error">Error: ${escapeHtml(error.message)}</p>
      <button class="primary" id="generateTool">Retry</button>
    `;
    $('#generateTool').onclick = () => runTool(tool, scope);
  }
}

function renderToolResult(tool, content) {
  const safeName = state.chatTitle.replace(/[^\w\s-]/g, '') || 'study';
  let toolbar = `
    <div class="tool-toolbar">
      <button type="button" id="toolCopy">Copy</button>
      <button type="button" id="toolDownload">Download .md</button>
  `;
  if (tool === 'quiz' && parseQuizMarkdown(content).length > 0) {
    toolbar += `<button type="button" class="primary" id="toolStudy">Take quiz</button>`;
  }
  if (tool === 'flashcards' && parseFlashcardsMarkdown(content).length > 0) {
    toolbar += `<button type="button" class="primary" id="toolStudy">Study flashcards</button>`;
  }
  if (tool === 'glossary' && parseGlossaryMarkdown(content).length > 0) {
    toolbar += `<button type="button" class="primary" id="toolStudy">Study glossary</button>`;
  }
  toolbar += '</div>';

  $('#toolContent').innerHTML =
    `<div class="tool-result markdown-output" id="toolMarkdown">${marked.parse(content)}</div>${toolbar}`;

  $('#toolCopy').onclick = () => copyToClipboard(content);
  $('#toolDownload').onclick = () => downloadText(`${safeName}-${tool}.md`, content);
  const studyBtn = $('#toolStudy');
  if (studyBtn) {
    studyBtn.onclick = () => {
      if (tool === 'quiz') startQuizPlayer(parseQuizMarkdown(content));
      else if (tool === 'flashcards') startFlashcardPlayer(parseFlashcardsMarkdown(content));
      else if (tool === 'glossary') startGlossaryPlayer(parseGlossaryMarkdown(content));
    };
  }
}

// ── Tool history (past quizzes, flashcards, summaries, glossaries) ────────

function scopeLabel(scope) {
  if (scope.topic) return `Topic: ${scope.topic}`;
  const ids = scope.sourceIds || [];
  if (ids.length > 1) return `All ${ids.length} sources`;
  const match = state.sources.find((s) => s.sourceId === ids[0]);
  return match ? match.name : state.sourceName;
}

function pushToolHistory(tool, content, scope) {
  if (!state.toolHistory) state.toolHistory = [];
  state.toolHistory.unshift({
    id: `h_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
    tool,
    label: scopeLabel(scope),
    count: scope.count || null,
    content,
    createdAt: Date.now(),
  });
  if (state.toolHistory.length > 50) state.toolHistory.length = 50;
  persistState();
}

const TOOL_ICONS = { quiz: 'quiz', flashcards: 'style', summary: 'summarize', glossary: 'menu_book', compare: 'compare_arrows' };
const TOOL_LABELS = { quiz: 'Quiz', flashcards: 'Flashcards', summary: 'Summary', glossary: 'Glossary', compare: 'Compare' };

function openHistory() {
  const hasSources = (state.sources && state.sources.length > 0) || state.sourceId;
  if (!hasSources) {
    alert('Please open a chat with a source first.');
    return;
  }
  renderHistoryList();
  openModal(historyModal);
}

function formatHistoryDate(ts) {
  if (!ts) return '';
  return new Date(ts).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}

function renderHistoryList() {
  const items = (state.toolHistory || []).filter((h) => historyFilter === 'all' || h.tool === historyFilter);
  const list = $('#historyList');
  if (items.length === 0) {
    list.innerHTML = `<p class="empty-recent">Nothing generated yet. Create a quiz, flashcards, summary, or glossary to see it here.</p>`;
    return;
  }
  list.innerHTML = items.map((h) => `
    <button type="button" class="history-item" data-id="${h.id}">
      <span class="material-symbols-outlined">${TOOL_ICONS[h.tool] || 'description'}</span>
      <span class="history-item-body">
        <strong>${TOOL_LABELS[h.tool] || h.tool}</strong>
        <small>${escapeHtml(h.label || '')}${h.count ? ` · ${h.count} items` : ''}</small>
        <small class="history-date">${formatHistoryDate(h.createdAt)}</small>
      </span>
      <span class="material-symbols-outlined history-arrow">chevron_right</span>
    </button>
  `).join('');
  list.querySelectorAll('.history-item').forEach((btn) => {
    btn.onclick = () => openHistoryEntry(btn.dataset.id);
  });
}

function openHistoryEntry(id) {
  const entry = (state.toolHistory || []).find((h) => h.id === id);
  if (!entry) return;
  closeModal(historyModal);
  $('#toolEyebrow').textContent = (entry.tool || '').toUpperCase();
  $('#toolTitle').textContent = `${TOOL_LABELS[entry.tool] || entry.tool} — ${entry.label || ''}`;
  renderToolResult(entry.tool, entry.content);
  openModal(toolModal);
}

// ── Interactive quiz ──────────────────────────────────────────────────────

function parseQuizMarkdown(md) {
  const questions = [];
  const blocks = md.split(/\n(?=\d+\.\s)/);
  for (const block of blocks) {
    const qMatch = block.match(/^\d+\.\s*(.+?)(?:\n|$)/s);
    if (!qMatch) continue;
    const question = qMatch[1].trim().replace(/\*\*/g, '');
    const options = {};
    for (const letter of ['A', 'B', 'C', 'D']) {
      const optMatch = block.match(new RegExp(`${letter}[.)\\]\\:]\\s*(.+?)(?:\\n|$)`, 'i'));
      if (optMatch) options[letter] = optMatch[1].trim().replace(/\*\*/g, '');
    }
    const ansMatch = block.match(/\*\*Answer:\s*([A-D])\*\*/i)
      || block.match(/Answer:\s*([A-D])/i);
    const expMatch = block.match(/Explanation:\s*\**\s*(.+?)(?:\n\s*\n|$)/is);
    const explanation = expMatch ? expMatch[1].trim().replace(/\*\*/g, '').replace(/\s+/g, ' ') : '';
    if (question && Object.keys(options).length >= 2 && ansMatch) {
      questions.push({ question, options, answer: ansMatch[1].toUpperCase(), explanation });
    }
  }
  return questions;
}

function startQuizPlayer(questions) {
  let index = 0;
  let score = 0;
  let answered = false;

  const render = () => {
    if (index >= questions.length) {
      $('#toolContent').innerHTML = `
        <div class="quiz-score">
          <h3>Done!</h3>
          <p>You scored <strong>${score} / ${questions.length}</strong></p>
          <div class="tool-toolbar">
            <button type="button" class="primary" id="quizRetry">Try again</button>
          </div>
        </div>
      `;
      $('#quizRetry').onclick = () => startQuizPlayer(questions);
      return;
    }

    const q = questions[index];
    const letters = Object.keys(q.options).sort();
    $('#toolContent').innerHTML = `
      <div class="quiz-player">
        <div class="quiz-progress">Question ${index + 1} of ${questions.length}</div>
        <div class="quiz-question">${escapeHtml(q.question)}</div>
        <div class="quiz-options" id="quizOptions">
          ${letters.map((l) => `
            <button type="button" class="quiz-option" data-letter="${l}">
              <strong>${l}.</strong> ${escapeHtml(q.options[l])}
            </button>
          `).join('')}
        </div>
        <div class="quiz-feedback hidden" id="quizFeedback"></div>
        <div class="quiz-nav">
          <button type="button" id="quizBack" ${index === 0 ? 'disabled' : ''}>Back</button>
          <button type="button" class="primary hidden" id="quizNext">Next</button>
        </div>
      </div>
    `;

    answered = false;
    $('#quizOptions').querySelectorAll('.quiz-option').forEach((btn) => {
      btn.onclick = () => {
        if (answered) return;
        answered = true;
        const picked = btn.dataset.letter;
        const correct = q.answer;
        if (picked === correct) score += 1;
        $('#quizOptions').querySelectorAll('.quiz-option').forEach((b) => {
          b.disabled = true;
          if (b.dataset.letter === correct) b.classList.add('correct');
          else if (b.dataset.letter === picked) b.classList.add('wrong');
        });
        const fb = $('#quizFeedback');
        fb.classList.remove('hidden');
        const verdict = picked === correct ? 'Correct!' : `Incorrect — answer is ${correct}.`;
        fb.innerHTML = `<span class="quiz-feedback-verdict">${escapeHtml(verdict)}</span>` +
          (q.explanation ? `<span class="quiz-feedback-explanation">${escapeHtml(q.explanation)}</span>` : '');
        fb.classList.toggle('is-correct', picked === correct);
        fb.classList.toggle('is-wrong', picked !== correct);
        $('#quizNext').classList.remove('hidden');
      };
    });
    $('#quizBack').onclick = () => { if (index > 0) { index -= 1; render(); } };
    $('#quizNext').onclick = () => { index += 1; render(); };
  };

  render();
}

// ── Flashcard study mode ──────────────────────────────────────────────────

function parseFlashcardsMarkdown(md) {
  const cards = [];
  const re = /\*\*Q:\*\*\s*(.+?)\s*\*\*A:\*\*\s*(.+?)(?=\*\*Q:\*\*|$)/gs;
  let m;
  while ((m = re.exec(md)) !== null) {
    cards.push({ q: m[1].trim(), a: m[2].trim() });
  }
  return cards;
}

function startFlipCardPlayer(items, { unitLabel, frontLabel, backLabel }) {
  let index = 0;
  let flipped = false;

  const render = () => {
    const item = items[index];
    $('#toolContent').innerHTML = `
      <div class="flashcard-player">
        <div class="quiz-progress">${unitLabel} ${index + 1} of ${items.length}</div>
        <div class="flashcard-container">
          <div class="flashcard${flipped ? ' flipped' : ''}" id="flashcard">
            <div class="flashcard-front">
              <div class="flashcard-text">${escapeHtml(item.front)}</div>
            </div>
            <div class="flashcard-back">
              <div class="flashcard-text">${escapeHtml(item.back)}</div>
            </div>
          </div>
        </div>
        <div class="flashcard-controls">
          <button type="button" id="fcPrev" ${index === 0 ? 'disabled' : ''}>Previous</button>
          <button type="button" class="primary" id="fcFlip">${flipped ? frontLabel : backLabel}</button>
          <button type="button" id="fcNext" ${index === items.length - 1 ? 'disabled' : ''}>Next</button>
          <button type="button" id="fcShuffle">Shuffle</button>
        </div>
      </div>
    `;

    const el = $('#flashcard');
    const flipBtn = $('#fcFlip');

    const performFlip = () => {
      flipped = !flipped;
      el.classList.toggle('flipped', flipped);
      flipBtn.textContent = flipped ? frontLabel : backLabel;
    };

    el.onclick = performFlip;
    flipBtn.onclick = performFlip;

    $('#fcPrev').onclick = () => {
      if (index > 0) {
        index -= 1;
        flipped = false;
        render();
      }
    };
    $('#fcNext').onclick = () => {
      if (index < items.length - 1) {
        index += 1;
        flipped = false;
        render();
      }
    };
    $('#fcShuffle').onclick = () => {
      for (let i = items.length - 1; i > 0; i -= 1) {
        const j = Math.floor(Math.random() * (i + 1));
        [items[i], items[j]] = [items[j], items[i]];
      }
      index = 0;
      flipped = false;
      render();
    };
  };

  render();
}

function startFlashcardPlayer(cards) {
  startFlipCardPlayer(
    cards.map((c) => ({ front: c.q, back: c.a })),
    { unitLabel: 'Card', frontLabel: 'Show question', backLabel: 'Show answer' },
  );
}

// ── Interactive glossary ────────────────────────────────────────────────────

function parseGlossaryMarkdown(md) {
  const terms = [];
  const rows = md.split('\n').map((l) => l.trim()).filter((l) => l.startsWith('|'));
  for (const row of rows) {
    if (/^\|?\s*:?-{2,}:?\s*\|/.test(row)) continue; // separator row
    const cells = row.split('|').map((c) => c.trim()).filter((c) => c.length > 0);
    if (cells.length < 2) continue;
    const term = cells[0].replace(/\*\*/g, '').trim();
    const definition = cells.slice(1).join(' — ').replace(/\*\*/g, '').trim();
    if (!term || !definition) continue;
    if (/^term$/i.test(term) || /^definition$/i.test(definition)) continue; // header row
    terms.push({ term, definition });
  }
  return terms;
}

function startGlossaryPlayer(terms) {
  startFlipCardPlayer(
    terms.map((t) => ({ front: t.term, back: t.definition })),
    { unitLabel: 'Term', frontLabel: 'Show term', backLabel: 'Show definition' },
  );
}

// ── Session persistence ─────────────────────────────────────────────────────

function loadChats() {
  try {
    let chats = JSON.parse(localStorage.getItem('sirius_chats'));
    if (!chats) {
      chats = [];
      const oldState = JSON.parse(localStorage.getItem('sirius_state') || 'null');
      const oldMessages = JSON.parse(localStorage.getItem('sirius_messages') || '[]');
      if (oldState && oldState.sourceId) {
        const migratedChat = {
          id: `chat_${Date.now()}`,
          sourceId: oldState.sourceId,
          sourceName: oldState.sourceName,
          chatTitle: oldState.chatTitle,
          sources: [{ sourceId: oldState.sourceId, name: oldState.sourceName }],
          messages: oldMessages,
        };
        chats.push(migratedChat);
        localStorage.setItem('sirius_chats', JSON.stringify(chats));
        localStorage.setItem('sirius_active_chat_id', migratedChat.id);
        localStorage.removeItem('sirius_state');
        localStorage.removeItem('sirius_messages');
      }
    }
    return chats;
  } catch (_e) {
    return [];
  }
}

function saveChats(chats) {
  try {
    localStorage.setItem('sirius_chats', JSON.stringify(chats));
  } catch (_e) {
    // ignore quota errors
  }
}

function persistState() {
  if (!state.chatId) return;
  try {
    const messages = Array.from(
      $('#messages').querySelectorAll('.user-message, .assistant-message')
    ).map((el) => ({
      role: el.classList.contains('user-message') ? 'user' : 'assistant',
      html: el.innerHTML,
      plainText: el.dataset.plainText || undefined,
    }));

    const chats = loadChats();
    const existingIndex = chats.findIndex((c) => c.id === state.chatId);
    const chatData = {
      id: state.chatId,
      sourceId: state.sourceId,
      sourceName: state.sourceName,
      chatTitle: state.chatTitle,
      sources: state.sources,
      messages,
      toolHistory: state.toolHistory || [],
    };

    if (existingIndex !== -1) chats[existingIndex] = chatData;
    else chats.unshift(chatData);

    saveChats(chats);
    localStorage.setItem('sirius_active_chat_id', state.chatId);
    renderSidebar();
  } catch (_e) {
    // ignore
  }
}

function restoreSession() {
  try {
    const activeChatId = localStorage.getItem('sirius_active_chat_id');
    const chats = loadChats();
    if (chats.length === 0) {
      renderSidebar();
      return;
    }

    const activeChat = chats.find((c) => c.id === activeChatId) || chats[0];
    state.chatId = activeChat.id;
    state.sourceId = activeChat.sourceId;
    state.sourceName = activeChat.sourceName;
    state.chatTitle = activeChat.chatTitle;
    state.sources = activeChat.sources && activeChat.sources.length ? activeChat.sources : [];
    state.toolHistory = activeChat.toolHistory || [];

    localStorage.setItem('sirius_active_chat_id', state.chatId);
    openWorkspace(activeChat.messages);
  } catch (_e) {
    localStorage.removeItem('sirius_chats');
    localStorage.removeItem('sirius_active_chat_id');
    renderSidebar();
  }
}

restoreSession();