/* Enterprise Knowledge Base — SPA Application */
const API = '/api/v1';
let state = { user: null, token: localStorage.getItem('token'), documents: [], users: [], stats: {} };

// ─── HTTP Client ──────────────────────────────────────────
async function api(path, options = {}) {
  const headers = { 'Content-Type': 'application/json', ...options.headers };
  if (state.token) headers['Authorization'] = `Bearer ${state.token}`;
  const res = await fetch(`${API}${path}`, { ...options, headers });
  if (res.status === 401) { logout(); return null; }
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || 'Request failed');
  return data;
}

function toast(msg, type = 'success') {
  const t = document.createElement('div');
  t.className = `toast toast-${type}`;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3000);
}

// ─── Auth ──────────────────────────────────────────────────
async function login(e) {
  e.preventDefault();
  const form = e.target;
  try {
    const data = await api('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username: form.username.value, password: form.password.value })
    });
    state.token = data.access_token;
    localStorage.setItem('token', data.token || data.access_token);
    state.user = data.user || data;
    await loadApp();
  } catch (err) { showAlert('auth-alert', err.message, 'error'); }
}

async function register(e) {
  e.preventDefault();
  const form = e.target;
  try {
    await api('/auth/register', {
      method: 'POST',
      body: JSON.stringify({ username: form.username.value, email: form.email.value, password: form.password.value })
    });
    showAlert('reg-alert', '注册成功，请登录', 'success');
    setTimeout(() => showPage('login'), 1000);
  } catch (err) { showAlert('reg-alert', err.message, 'error'); }
}

function showAlert(id, msg, type) {
  const el = document.getElementById(id);
  if (!el) return;
  el.className = `alert alert-${type}`;
  el.textContent = msg;
  el.style.display = 'block';
}

function logout() {
  state.token = null; state.user = null; localStorage.removeItem('token');
  document.querySelector('.app').classList.remove('active');
  document.getElementById('auth-page').style.display = 'flex';
  showPage('login');
}

// ─── Navigation ────────────────────────────────────────────
function showPage(name) {
  document.querySelectorAll('.auth-page, .page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  const page = document.getElementById(`page-${name}`);
  if (page) page.classList.add('active');
  const nav = document.querySelector(`[data-page="${name}"]`);
  if (nav) nav.classList.add('active');
  if (name === 'chat') scrollChat();
  if (name === 'docs') loadDocuments();
  if (name === 'admin') loadAdmin();
}

// ─── App Load ──────────────────────────────────────────────
async function loadApp() {
  try {
    state.user = await api('/auth/me');
    document.getElementById('auth-page').style.display = 'none';
    document.querySelector('.app').classList.add('active');
    document.querySelector('.user-info').textContent = `${state.user.username} (${state.user.role})`;
    showPage('chat');
  } catch { logout(); }
}

// Check token on load
if (state.token) { document.addEventListener('DOMContentLoaded', loadApp); }

// ─── Chat ──────────────────────────────────────────────────
let chatHistory = [];

async function sendMessage(e) {
  e.preventDefault();
  const input = document.getElementById('chat-input');
  const msg = input.value.trim();
  if (!msg) return;

  // Add user message
  addMessage(msg, 'user');
  input.value = '';
  input.style.height = 'auto';

  // Show typing
  const typing = document.getElementById('typing-indicator');
  typing.style.display = 'flex';

  // Remove welcome
  document.getElementById('welcome').style.display = 'none';

  try {
    const data = await api('/chat/query', {
      method: 'POST',
      body: JSON.stringify({ question: msg, top_k: 5 })
    });
    typing.style.display = 'none';
    addAssistantMessage(data.answer, data.sources || [], data);
  } catch (err) {
    typing.style.display = 'none';
    addAssistantMessage('抱歉，查询出错: ' + err.message, [], { model_used: null });
  }
}

function addMessage(text, role) {
  const div = document.createElement('div');
  div.className = `message ${role}`;
  div.textContent = text;
  document.getElementById('chat-messages').appendChild(div);
  scrollChat();
}

function addAssistantMessage(text, sources, meta) {
  // meta is optional { model_used: { id, name, provider } }
  const div = document.createElement('div');
  div.className = 'message assistant';

  // Format text with markdown-like rendering
  const formatted = text.replace(/\n/g, '<br>').replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
  div.innerHTML = formatted;

  // Add sources
  if (sources && sources.length > 0) {
    const details = document.createElement('details');
    details.className = 'sources';
    const summary = document.createElement('summary');
    summary.textContent = `📚 ${sources.length} 个参考来源`;
    details.appendChild(summary);
    sources.forEach(s => {
      const item = document.createElement('div');
      item.className = 'source-item';
      item.innerHTML = `<span class="score">${(s.score * 100).toFixed(0)}%</span> ${s.title || '文档'} — ${(s.snippet || '').substring(0, 100)}...`;
      details.appendChild(item);
    });
    div.appendChild(details);
  }

  // Phase 6.5 — model tag + feedback buttons
  const footer = document.createElement('div');
  footer.className = 'feedback-row';

  const modelName = meta?.model_used?.name || (meta?.model_used?.id != null ? `#${meta.model_used.id}` : 'unknown');
  const tag = document.createElement('span');
  tag.className = 'model-tag';
  tag.textContent = `via ${modelName}`;
  footer.appendChild(tag);

  const upBtn = document.createElement('button');
  upBtn.type = 'button';
  upBtn.className = 'fb-btn';
  upBtn.title = '有用';
  upBtn.textContent = '👍';
  const downBtn = document.createElement('button');
  downBtn.type = 'button';
  downBtn.className = 'fb-btn';
  downBtn.title = '没帮助';
  downBtn.textContent = '👎';
  footer.appendChild(upBtn);
  footer.appendChild(downBtn);

  div.appendChild(footer);

  // Wire feedback handlers (clicks use closure over meta)
  // Phase 6 fix: 用 ABTestMetric 主键 (metric_id) 而非 ModelConfig.id
  const payload = { metric_id: meta?.model_used?.metric_id ?? meta?.model_used?.id };
  upBtn.addEventListener('click', () => submitFeedback(payload, 1, null, upBtn, downBtn));
  downBtn.addEventListener('click', () => {
    const text = prompt('请描述一下哪里没帮助 (可选):');
    submitFeedback(payload, -1, text || null, upBtn, downBtn);
  });

  document.getElementById('chat-messages').appendChild(div);
  scrollChat();
}

async function submitFeedback(payload, value, text, upBtn, downBtn) {
  // Backend Pydantic: { metric_id: int, feedback: -1|0|1, feedback_text?: str }
  const body = { ...payload, feedback: value };
  if (text) body.feedback_text = text;
  try {
    await api('/chat/feedback', { method: 'POST', body: JSON.stringify(body) });
    toast(value > 0 ? '👍 已记录反馈' : '👎 已记录反馈，谢谢');
    upBtn.disabled = true;
    downBtn.disabled = true;
    if (value > 0) upBtn.classList.add('active-up');
    else downBtn.classList.add('active-down');
  } catch (e) {
    toast('反馈失败: ' + e.message, 'error');
  }
}

function scrollChat() {
  const el = document.getElementById('chat-messages');
  setTimeout(() => el.scrollTop = el.scrollHeight, 50);
}

// Auto-resize textarea
document.addEventListener('input', e => {
  if (e.target.id === 'chat-input') {
    e.target.style.height = 'auto';
    e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px';
  }
});

// ─── Documents ─────────────────────────────────────────────
let editingDocId = null;

async function loadDocuments() {
  try {
    state.documents = await api('/documents?skip=0&limit=50');
    renderDocuments();
  } catch { }
}

function renderDocuments() {
  const grid = document.getElementById('doc-grid');
  const empty = document.getElementById('docs-empty');
  grid.innerHTML = '';
  const docs = Array.isArray(state.documents) ? state.documents : (state.documents.data || state.documents.items || []);
  if (docs.length === 0) { empty.style.display = 'block'; return; }
  empty.style.display = 'none';
  docs.forEach(doc => {
    const card = document.createElement('div');
    card.className = 'doc-card';
    const ext = (doc.file_type || doc.filename?.split('.').pop() || 'md').toLowerCase();
    card.innerHTML = `
      <h3>${doc.title || doc.filename}</h3>
      <div class="meta">${(doc.file_size / 1024).toFixed(0)} KB · ${doc.chunk_count || 0} 切片</div>
      <span class="badge badge-${ext}">${ext.toUpperCase()}</span>
      <div class="status ${doc.is_indexed ? 'status-indexed' : 'status-pending'}">${doc.is_indexed ? '✅ 已索引' : '⏳ 待索引'}</div>
      <div class="meta" style="margin-top:6px">${doc.created_at ? new Date(doc.created_at).toLocaleString() : ''}</div>
    `;
    card.onclick = () => showDocDetail(doc);
    grid.appendChild(card);
  });
}

function showUploadModal() {
  editingDocId = null;
  document.getElementById('modal-title').textContent = '上传文档';
  document.getElementById('upload-form').reset();
  document.getElementById('file-info').style.display = 'none';
  document.getElementById('upload-progress').style.display = 'none';
  document.querySelector('.modal-overlay').classList.add('active');
}

function closeModal() {
  document.querySelector('.modal-overlay').classList.remove('active');
}

let selectedFile = null;

function onFileSelect(e) {
  selectedFile = e.target.files[0] || (e.dataTransfer?.files[0]);
  if (!selectedFile) return;
  const info = document.getElementById('file-info');
  info.style.display = 'block';
  info.innerHTML = `📄 ${selectedFile.name} (${(selectedFile.size / 1024).toFixed(0)} KB)`;
}

async function uploadDoc(e) {
  e.preventDefault();
  if (!selectedFile) return toast('请选择文件', 'error');
  const formData = new FormData();
  formData.append('file', selectedFile);
  const title = document.getElementById('doc-title').value || selectedFile.name.replace(/\.[^/.]+$/, '');
  formData.append('title', title);

  const progress = document.getElementById('upload-progress');
  progress.style.display = 'block';
  const bar = progress.querySelector('.fill');

  try {
    // Upload via direct fetch
    const res = await fetch(`${API}/documents/upload`, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${state.token}` },
      body: formData
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Upload failed');

    bar.style.width = '100%';
    toast('上传成功！正在索引...');

    // Auto-index
    await api(`/documents/${data.id}/index`, { method: 'POST' });
    toast('索引完成 ✅');
    closeModal();
    loadDocuments();
  } catch (err) {
    toast(err.message, 'error');
  }
}

function showDocDetail(doc) {
  // Simple detail view
  toast(`${doc.title || doc.filename} — ${doc.chunk_count || 0} 个切片，${doc.is_indexed ? '已索引' : '未索引'}`);
}

// ─── Admin ─────────────────────────────────────────────────
async function loadAdmin() {
  try {
    state.stats = await api('/admin/stats');
    state.users = await api('/admin/users');
    renderAdmin();
  } catch { }
}

function renderAdmin() {
  // Stats
  const grid = document.getElementById('stats-grid');
  const stats = state.stats || {};
  const items = [
    { value: stats.user_count || stats.users || 0, label: '用户', icon: '👥' },
    { value: stats.document_count || stats.documents || 0, label: '文档', icon: '📄' },
    { value: stats.chunk_count || stats.chunks || 0, label: '向量切片', icon: '🧩' },
    { value: stats.query_count || stats.queries || 0, label: '问答次数', icon: '💬' }
  ];
  grid.innerHTML = items.map(i => `
    <div class="stat-card"><div class="value">${i.value}</div><div class="label">${i.icon} ${i.label}</div></div>
  `).join('');

  // Users table
  const tbody = document.getElementById('users-table-tbody');
  const users = Array.isArray(state.users) ? state.users : (state.users.data || state.users.items || []);
  tbody.innerHTML = users.map(u => `
    <tr>
      <td>${u.id}</td>
      <td>${u.username}</td>
      <td>${u.email}</td>
      <td><span class="role-badge role-${u.role}">${u.role}</span></td>
      <td>${u.is_active ? '✅' : '❌'}</td>
      <td>${u.organization || '-'}</td>
      <td>
        <select onchange="changeRole(${u.id}, this.value)">
          <option value="admin" ${u.role==='admin'?'selected':''}>管理员</option>
          <option value="editor" ${u.role==='editor'?'selected':''}>编辑者</option>
          <option value="viewer" ${u.role==='viewer'?'selected':''}>查看者</option>
        </select>
        <button class="btn btn-danger btn-sm" onclick="deleteUser(${u.id})" ${u.id===state.user.id?'disabled':''}>删除</button>
      </td>
    </tr>
  `).join('');
}

async function changeRole(userId, role) {
  try {
    await api(`/admin/users/${userId}/role`, { method: 'PUT', body: JSON.stringify({ role }) });
    toast('角色已更新');
  } catch (err) { toast(err.message, 'error'); }
}

async function deleteUser(userId) {
  if (!confirm('确定删除此用户？')) return;
  try {
    await api(`/admin/users/${userId}`, { method: 'DELETE' });
    toast('用户已删除');
    loadAdmin();
  } catch (err) { toast(err.message, 'error'); }
}

// ─── Drag & Drop ───────────────────────────────────────────
const dropzone = document.querySelector('.dropzone');
if (dropzone) {
  dropzone.addEventListener('dragover', e => { e.preventDefault(); dropzone.classList.add('dragover'); });
  dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
  dropzone.addEventListener('drop', e => { e.preventDefault(); dropzone.classList.remove('dragover'); onFileSelect(e); });
}
