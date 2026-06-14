/* Phase 6.3 — /admin/ab-tests page
 * Lists A/B rules, lets admin create/edit/toggle/delete.
 * Backend: GET/POST /api/v1/admin/ab-rules, PATCH/{id}, DELETE/{id}
 */
window.AdminABTests = (function () {
  const root = () => document.getElementById('admin-ab-tests-root');

  // Backend allows: strategy ∈ {user_hash_mod, random_weight}, target ∈ {chat, embedding}
  const STRATEGIES = ['user_hash_mod', 'random_weight'];
  const TARGETS    = ['chat', 'embedding'];

  let rules = [];

  function el(tag, props = {}, children = []) {
    const e = document.createElement(tag);
    Object.entries(props).forEach(([k, v]) => {
      if (k === 'class') e.className = v;
      else if (k === 'style') e.style.cssText = v;
      else if (k.startsWith('on') && typeof v === 'function') e.addEventListener(k.slice(2), v);
      else if (k === 'checked') e.checked = !!v;
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
      const data = await api('/admin/ab-rules');
      rules = Array.isArray(data) ? data : (data.data || data.items || []);
    } catch (e) {
      toast('加载失败: ' + e.message, 'error');
      rules = [];
    }
  }

  function toggleSwitch(rule) {
    const on = !rule.enabled;
    const sw = el('label', { class: 'switch', title: on ? '已启用，点击停用' : '已停用，点击启用' });
    const input = el('input', { type: 'checkbox', checked: !!rule.enabled });
    input.addEventListener('change', async () => {
      try {
        await api(`/admin/ab-rules/${rule.id}`, {
          method: 'PATCH',
          body: JSON.stringify({ enabled: input.checked }),
        });
        toast(input.checked ? '已启用' : '已停用');
        rule.enabled = input.checked;
      } catch (e) {
        toast('切换失败: ' + e.message, 'error');
        input.checked = !input.checked; // revert
      }
    });
    sw.appendChild(input);
    sw.appendChild(el('span', { class: 'slider' }));
    return sw;
  }

  function renderList() {
    const wrap = el('div', { class: 'admin-spa' });
    wrap.appendChild(el('h1', {}, '🧪 A/B 规则'));

    const toolbar = el('div', { class: 'toolbar' }, [
      el('button', { class: 'btn btn-primary', onclick: () => openEditor(null) }, '+ 新建规则'),
      el('span', { style: 'color:var(--gray-500);font-size:12px' }, `共 ${rules.length} 条`),
    ]);
    wrap.appendChild(toolbar);

    // Header row
    wrap.appendChild(el('div', { class: 'model-row header' }, [
      el('div', {}, '规则名'),
      el('div', {}, 'Target'),
      el('div', {}, 'Strategy'),
      el('div', {}, '启用'),
      el('div', {}, '操作'),
    ]));

    if (rules.length === 0) {
      wrap.appendChild(el('div', { style: 'padding:24px;color:var(--gray-500);text-align:center' }, '暂无规则，点上方按钮创建'));
    }

    rules.forEach(r => {
      wrap.appendChild(el('div', { class: 'model-row' }, [
        el('div', {}, [
          el('div', { style: 'font-weight:500' }, r.name),
          r.description ? el('div', { style: 'font-size:11px;color:var(--gray-500)' }, r.description) : null,
        ]),
        el('div', {}, el('span', { class: 'badge-cap ' + (r.target === 'chat' ? 'badge-chat' : 'badge-embedding') }, r.target)),
        el('div', {}, el('span', { class: 'badge-cap' }, r.strategy)),
        el('div', {}, toggleSwitch(r)),
        el('div', { style: 'display:flex;gap:4px' }, [
          el('button', { class: 'btn btn-sm', onclick: () => openEditor(r) }, '编辑'),
          el('button', { class: 'btn btn-sm btn-danger', onclick: () => deleteRule(r) }, '删除'),
        ]),
      ]));
    });

    return wrap;
  }

  function openEditor(rule) {
    // Remove any existing modal
    const existing = document.getElementById('ab-editor-modal');
    if (existing) existing.remove();

    const isEdit = !!rule;
    const overlay = el('div', { class: 'modal-overlay active', id: 'ab-editor-modal' });
    overlay.addEventListener('click', e => { if (e.target === overlay) closeEditor(); });

    const modal = el('div', { class: 'modal', style: 'max-width:520px' });
    modal.appendChild(el('h2', {}, isEdit ? '编辑规则' : '新建规则'));

    // name
    const nameInput = el('input', { type: 'text', value: rule ? rule.name : '', placeholder: '规则名 (1-128 字符)', style: 'width:100%;padding:8px 10px;border:1px solid var(--gray-300);border-radius:var(--radius);margin-bottom:10px' });
    const nameGroup = el('div', { class: 'form-group' }, [el('label', {}, '规则名'), nameInput]);
    modal.appendChild(nameGroup);

    // target select
    const targetSelect = el('select', { style: 'width:100%;padding:8px 10px;border:1px solid var(--gray-300);border-radius:var(--radius);margin-bottom:10px' });
    TARGETS.forEach(t => {
      const opt = el('option', { value: t }, t);
      if (rule && rule.target === t) opt.selected = true;
      targetSelect.appendChild(opt);
    });
    modal.appendChild(el('div', { class: 'form-group' }, [el('label', {}, 'Target'), targetSelect]));

    // strategy select
    const stratSelect = el('select', { style: 'width:100%;padding:8px 10px;border:1px solid var(--gray-300);border-radius:var(--radius);margin-bottom:10px' });
    STRATEGIES.forEach(s => {
      const opt = el('option', { value: s }, s);
      if (rule && rule.strategy === s) opt.selected = true;
      stratSelect.appendChild(opt);
    });
    modal.appendChild(el('div', { class: 'form-group' }, [el('label', {}, 'Strategy'), stratSelect]));

    // description (optional)
    const descInput = el('input', { type: 'text', value: rule ? (rule.description || '') : '', placeholder: '描述 (可选)', style: 'width:100%;padding:8px 10px;border:1px solid var(--gray-300);border-radius:var(--radius);margin-bottom:10px' });
    modal.appendChild(el('div', { class: 'form-group' }, [el('label', {}, '描述'), descInput]));

    // config JSONB
    const cfgTextarea = el('textarea', { class: 'json', placeholder: 'config (JSON)' });
    cfgTextarea.value = rule ? JSON.stringify(rule.config, null, 2) : defaultConfigFor(stratSelect.value);
    modal.appendChild(el('div', { class: 'form-group' }, [
      el('label', {}, 'Config (JSONB)'),
      cfgTextarea,
      el('div', { style: 'font-size:11px;color:var(--gray-500);margin-top:4px' }, configHint(stratSelect.value)),
    ]));

    // Update config placeholder when strategy changes
    stratSelect.addEventListener('change', () => {
      if (!cfgTextarea.value.trim() || cfgTextarea.value === JSON.stringify(rule?.config || {}, null, 2)) {
        cfgTextarea.value = defaultConfigFor(stratSelect.value);
      }
      // refresh hint
      const hint = modal.querySelector('.config-hint');
      if (hint) hint.textContent = configHint(stratSelect.value);
    });

    // Actions
    const submitBtn = el('button', { class: 'btn btn-primary', type: 'button' }, isEdit ? '保存' : '创建');
    submitBtn.addEventListener('click', () => submitRule(rule, nameInput.value.trim(), targetSelect.value, stratSelect.value, cfgTextarea.value, descInput.value.trim()));
    const cancelBtn = el('button', { class: 'btn', type: 'button', style: 'background:var(--gray-100)' }, '取消');
    cancelBtn.addEventListener('click', closeEditor);

    modal.appendChild(el('div', { style: 'display:flex;gap:8px;margin-top:16px' }, [submitBtn, cancelBtn]));
    overlay.appendChild(modal);
    document.body.appendChild(overlay);
    nameInput.focus();
  }

  function configHint(strategy) {
    if (strategy === 'user_hash_mod') {
      return '示例: {"mod": 2, "mapping": {"0": <model_id>, "1": <model_id>}} — mod 必须是正整数，mapping key 必须覆盖 0..mod-1';
    }
    if (strategy === 'random_weight') {
      return '示例: {"models": [{"model_id": <id>, "weight": 0.5}, {"model_id": <id>, "weight": 0.5}]}';
    }
    return '';
  }

  function defaultConfigFor(strategy) {
    if (strategy === 'user_hash_mod') return JSON.stringify({ mod: 2, mapping: { '0': 0, '1': 0 } }, null, 2);
    if (strategy === 'random_weight') return JSON.stringify({ models: [{ model_id: 0, weight: 0.5 }, { model_id: 0, weight: 0.5 }] }, null, 2);
    return '{}';
  }

  function closeEditor() {
    const m = document.getElementById('ab-editor-modal');
    if (m) m.remove();
  }

  async function submitRule(rule, name, target, strategy, configJson, description) {
    if (!name) { toast('规则名必填', 'error'); return; }
    let config;
    try { config = JSON.parse(configJson || '{}'); }
    catch (e) { toast('Config 不是合法 JSON: ' + e.message, 'error'); return; }

    const body = { name, target, strategy, config };
    if (description) body.description = description;

    try {
      if (rule) {
        await api(`/admin/ab-rules/${rule.id}`, { method: 'PATCH', body: JSON.stringify(body) });
        toast('已更新');
      } else {
        await api('/admin/ab-rules', { method: 'POST', body: JSON.stringify(body) });
        toast('已创建');
      }
      closeEditor();
      await load();
      render();
    } catch (e) {
      toast('保存失败: ' + e.message, 'error');
    }
  }

  async function deleteRule(rule) {
    if (!confirm(`确定删除规则 "${rule.name}"？`)) return;
    try {
      await api(`/admin/ab-rules/${rule.id}`, { method: 'DELETE' });
      toast('已删除');
      await load();
      render();
    } catch (e) {
      toast('删除失败: ' + e.message, 'error');
    }
  }

  async function render() {
    const r = root();
    if (!r) return;
    r.innerHTML = '<div class="admin-spa"><h1>🧪 A/B 规则</h1><div>加载中...</div></div>';
    await load();
    r.innerHTML = '';
    r.appendChild(renderList());
  }

  return { render };
})();
