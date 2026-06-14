/* Phase 6.2 — /admin/models page
 * Renders providers + models + connectivity test + default toggle.
 */
window.AdminModels = (function () {
  const root = () => document.getElementById('admin-models-root');

  let providers = [];
  let models = [];

  function el(tag, props = {}, children = []) {
    const e = document.createElement(tag);
    Object.entries(props).forEach(([k, v]) => {
      if (k === 'class') e.className = v;
      else if (k === 'style') e.style.cssText = v;
      else if (k.startsWith('on') && typeof v === 'function') e.addEventListener(k.slice(2), v);
      else if (v !== undefined && v !== null) e.setAttribute(k, v);
    });
    (Array.isArray(children) ? children : [children]).forEach(c => {
      if (c == null) return;
      e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    });
    return e;
  }

  async function load() {
    try {
      const [ps, ms] = await Promise.all([
        api('/admin/providers'),
        api('/admin/models'),
      ]);
      providers = Array.isArray(ps) ? ps : (ps.data || ps.items || []);
      models = Array.isArray(ms) ? ms : (ms.data || ms.items || []);
    } catch (e) {
      toast('加载失败: ' + e.message, 'error');
      providers = []; models = [];
    }
  }

  function renderProviders() {
    const wrap = el('div');
    wrap.appendChild(el('h2', {}, 'Providers'));

    providers.forEach(p => {
      const card = el('div', { class: 'provider-card' + (p.is_builtin ? ' builtin' : '') }, [
        el('div', { style: 'display:flex;justify-content:space-between;align-items:center' }, [
          el('div', {}, [
            el('strong', {}, p.display_name || p.name),
            el('span', { class: 'badge-cap', style: 'margin-left:8px' }, p.provider_type),
            p.is_builtin ? el('span', { class: 'badge-cap', style: 'margin-left:6px;background:#dbeafe;color:#1e40af' }, '内置') : null,
          ]),
          el('div', { style: 'display:flex;gap:6px' }, [
            el('span', { class: 'badge-cap' }, p.enabled ? '已启用' : '已禁用'),
            !p.is_builtin ? el('button', { class: 'btn btn-sm', onclick: () => editProvider(p) }, '编辑') : null,
            !p.is_builtin ? el('button', { class: 'btn btn-sm btn-danger', onclick: () => deleteProvider(p) }, '删除') : null,
          ]),
        ]),
        el('div', { style: 'font-size:12px;color:var(--gray-500);margin-top:6px' },
          `key **** ${p.key_last_4 || ''} · ${p.api_base_url || '默认端点'}`),
      ]);
      wrap.appendChild(card);
    });

    const addBtn = el('button', { class: 'btn btn-primary', onclick: newProvider }, '+ 添加 Provider');
    wrap.appendChild(addBtn);
    return wrap;
  }

  function renderModels() {
    const wrap = el('div');
    wrap.appendChild(el('h2', {}, 'Models'));

    const header = el('div', { class: 'model-row header' }, [
      el('div', {}, '名称'), el('div', {}, 'Provider'), el('div', {}, 'Capability'),
      el('div', {}, '活跃'), el('div', {}, '默认'), el('div', {}, '操作'),
    ]);
    wrap.appendChild(header);

    if (models.length === 0) {
      wrap.appendChild(el('div', { style: 'padding:16px;color:var(--gray-500)' }, '暂无模型'));
    }

    models.forEach(m => {
      const prov = providers.find(p => p.id === m.provider_id);
      const capClass = m.model_type === 'chat' ? 'badge-chat' : (m.model_type === 'embedding' ? 'badge-embedding' : '');
      const isDefaultCap = m.model_type === 'chat' ? m.is_default_chat : m.is_default_emb;
      const row = el('div', { class: 'model-row' }, [
        el('div', {}, [
          el('div', { style: 'font-weight:500' }, m.display_name || m.model_name),
          el('div', { style: 'font-size:11px;color:var(--gray-500)' }, m.model_name),
        ]),
        el('div', {}, prov ? prov.name : `#${m.provider_id}`),
        el('div', {}, el('span', { class: 'badge-cap ' + capClass }, m.model_type)),
        el('div', {}, m.enabled ? '✅' : '❌'),
        el('div', {}, el('button', {
          class: 'star' + (isDefaultCap ? ' on' : ''),
          title: isDefaultCap ? '当前默认' : '设为默认',
          onclick: () => toggleDefault(m),
        }, '★')),
        el('div', { style: 'display:flex;gap:4px' }, [
          el('button', { class: 'btn btn-sm', onclick: () => testModel(m) }, '测试'),
          el('button', { class: 'btn btn-sm', onclick: () => toggleEnabled(m) }, m.enabled ? '停用' : '启用'),
        ]),
      ]);
      wrap.appendChild(row);
    });
    return wrap;
  }

  async function testModel(m) {
    toast(`正在测试 ${m.model_name}...`);
    try {
      const res = await api('/admin/models/test', {
        method: 'POST',
        body: JSON.stringify({
          provider_id: m.provider_id,
          model_name: m.model_name,
          test_message: 'ping',
        }),
      });
      if (res && res.success) {
        toast(`✓ ${m.model_name}: ${res.latency_ms}ms`);
      } else {
        toast(`✗ ${m.model_name}: ${(res && res.error) || '测试失败'}`, 'error');
      }
    } catch (e) {
      toast(`✗ ${m.model_name}: ${e.message}`, 'error');
    }
  }

  async function toggleDefault(m) {
    const payload = {};
    if (m.model_type === 'chat') payload.is_default_chat = !m.is_default_chat;
    else if (m.model_type === 'embedding') payload.is_default_emb = !m.is_default_emb;
    else { toast('未知 capability，无法切默认', 'error'); return; }
    try {
      await api(`/admin/models/${m.id}`, { method: 'PATCH', body: JSON.stringify(payload) });
      toast('默认已切换');
      await load();
      render();
    } catch (e) {
      toast('切换失败: ' + e.message, 'error');
    }
  }

  async function toggleEnabled(m) {
    try {
      await api(`/admin/models/${m.id}`, { method: 'PATCH', body: JSON.stringify({ enabled: !m.enabled }) });
      await load();
      render();
    } catch (e) {
      toast('更新失败: ' + e.message, 'error');
    }
  }

  async function deleteProvider(p) {
    if (!confirm(`确定删除 provider "${p.name}"？`)) return;
    try {
      await api(`/admin/providers/${p.id}`, { method: 'DELETE' });
      toast('已删除');
      await load(); render();
    } catch (e) {
      toast('删除失败: ' + e.message, 'error');
    }
  }

  function newProvider() { editProvider(null); }
  function editProvider(p) {
    const isEdit = !!p;
    const name = prompt('Provider name (lowercase id)', p ? p.name : '');
    if (!name) return;
    const display = prompt('显示名', p ? p.display_name : name);
    if (!display) return;
    const type = prompt('provider_type (openai_compat / anthropic / gemini / minimax)', p ? p.provider_type : 'openai_compat');
    if (!type) return;
    const apiBase = prompt('API base URL (可空)', p ? (p.api_base_url || '') : '');
    const apiKey = isEdit ? null : prompt('API key (新建时必填)');
    if (!isEdit && !apiKey) return;
    const body = { name, display_name: display, provider_type: type };
    if (apiBase) body.api_base_url = apiBase;
    if (apiKey) body.api_key = apiKey;
    (async () => {
      try {
        if (isEdit) {
          await api(`/admin/providers/${p.id}`, { method: 'PATCH', body: JSON.stringify(body) });
        } else {
          await api('/admin/providers', { method: 'POST', body: JSON.stringify(body) });
        }
        toast(isEdit ? '已更新' : '已创建');
        await load(); render();
      } catch (e) {
        toast('保存失败: ' + e.message, 'error');
      }
    })();
  }

  async function render() {
    const r = root();
    if (!r) return;
    r.innerHTML = '<div class="admin-spa"><h1>⚙️ 模型管理</h1><div>加载中...</div></div>';
    await load();
    r.innerHTML = '';
    const wrap = el('div', { class: 'admin-spa' });
    wrap.appendChild(el('h1', {}, '⚙️ 模型管理'));
    wrap.appendChild(el('div', { class: 'grid-2' }, [
      renderProviders(),
      renderModels(),
    ]));
    r.appendChild(wrap);
  }

  return { render };
})();