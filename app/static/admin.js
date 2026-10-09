/* 管理后台交互逻辑（原生 JS，无需构建） */
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const state = { configured: false, users: [], pending: [], logs: [] };

  /* ---------------------------------------------------------------- 工具 */

  function alertBox(el, kind, message) {
    el.className = 'alert ' + kind;
    el.innerHTML = message;
    el.classList.remove('hidden');
  }

  function escapeHtml(text) {
    return String(text == null ? '' : text).replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
  }

  async function api(path, options = {}) {
    const res = await fetch(path, {
      credentials: 'same-origin',
      headers: options.body ? { 'Content-Type': 'application/json' } : {},
      ...options,
    });
    let data = {};
    try { data = await res.json(); } catch (e) { data = {}; }
    if (!res.ok || data.success === false) {
      throw new Error(data.message || data.detail || `请求失败 (${res.status})`);
    }
    return data;
  }

  function tag(kind, text) { return `<span class="tag ${kind}">${escapeHtml(text)}</span>`; }

  function approvalTag(user) {
    const s = user.approvalState;
    if (s === 'pending') return tag('warn', '待审批');
    if (s === 'approved') return tag('ok', '已批准');
    if (s === 'rejected') return tag('err', '已拒绝');
    return tag('skip', s || '-');
  }

  function statusTag(user) {
    if (user.approvalState !== 'approved') return '';
    if (!user.hasQueryPassword) return tag('warn', '未绑定');
    if (!user.enabled) return tag('err', '已停用');
    if (!user.credentialOk) return tag('warn', '凭据异常');
    return tag('ok', '自动签到中');
  }

  /* ---------------------------------------------------------------- 登录 */

  async function bootstrap() {
    try {
      const data = await api('/admin/api/status');
      state.configured = data.configured;
      $('adminWho').textContent = data.username || '';
      if (data.loggedIn) {
        $('logoutBtn').classList.remove('hidden');
        $('panel').classList.remove('hidden');
        $('loginCard').classList.add('hidden');
        await refreshAll();
      } else {
        $('loginCard').classList.remove('hidden');
        $('panel').classList.add('hidden');
        if (!data.configured) {
          $('loginTitle').textContent = '首次设置管理员';
          $('loginSub').textContent = '系统尚未配置管理员账号，请立即设置（密码至少 8 位）。';
        }
      }
    } catch (e) {
      $('loginCard').classList.remove('hidden');
    }
  }

  $('loginForm').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const box = $('loginAlert');
    const btn = $('loginBtn');
    btn.disabled = true;
    const path = state.configured ? '/admin/api/login' : '/admin/api/setup';
    try {
      await api(path, {
        method: 'POST',
        body: JSON.stringify({
          username: $('loginUsername').value.trim(),
          password: $('loginPassword').value,
        }),
      });
      $('loginForm').reset();
      await bootstrap();
    } catch (e) {
      alertBox(box, 'err', e.message);
    } finally {
      btn.disabled = false;
    }
  });

  $('logoutBtn').addEventListener('click', async () => {
    await api('/admin/api/logout', { method: 'POST' });
    state.configured = true;
    $('logoutBtn').classList.add('hidden');
    $('panel').classList.add('hidden');
    $('loginCard').classList.remove('hidden');
    $('adminWho').textContent = '';
  });

  /* ---------------------------------------------------------------- 标签页 */

  document.querySelectorAll('.tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach((t) => t.classList.remove('active'));
      document.querySelectorAll('.tab-panel').forEach((p) => p.classList.add('hidden'));
      tab.classList.add('active');
      $('tab-' + tab.dataset.tab).classList.remove('hidden');
      if (tab.dataset.tab === 'logs') loadLogs();
    });
  });

  /* ---------------------------------------------------------------- 数据 */

  async function refreshAll() {
    const data = await api('/admin/api/dashboard');
    state.users = data.users || [];
    state.pending = data.pending || [];
    renderStats(data);
    renderPending();
    renderMembers(state.users);

    if (state.pending.length) {
      const badge = $('pendingBadge');
      badge.textContent = state.pending.length;
      badge.classList.remove('hidden');
    } else {
      $('pendingBadge').classList.add('hidden');
    }
    await Promise.all([loadTerms(), loadHolidays()]);
  }

  function renderStats(data) {
    const s = data.stats || {};
    const today = data.today || {};
    const sched = data.scheduler || {};
    $('statsBox').innerHTML = `
      <div class="stat"><div class="k">成员总数</div><div class="v">${s.totalUsers || 0}</div></div>
      <div class="stat"><div class="k">已批准</div><div class="v ok">${s.approvedUsers || 0}</div></div>
      <div class="stat"><div class="k">待审批</div><div class="v ${s.pendingUsers ? 'err' : ''}">${s.pendingUsers || 0}</div></div>
      <div class="stat"><div class="k">今日签到成功</div><div class="v ok">${s.successToday || 0}</div></div>
      <div class="stat"><div class="k">今日签到失败</div><div class="v ${s.failedToday ? 'err' : ''}">${s.failedToday || 0}</div></div>
    `;

    const hint = $('calendarHint');
    hint.className = 'alert mt ' + (today.shouldSign ? 'ok' : 'warn');
    hint.innerHTML = `<b>${today.date}</b>：${today.shouldSign ? '今天会执行自动签到' : '今天不会执行自动签到'} — ${escapeHtml(today.reason || '')}`;

    const jobs = (sched.jobs || []).map((j) => `${j.id} → ${j.nextRun}`).join('；');
    $('schedulerHint').textContent = sched.running
      ? `调度器运行中。签到窗口 ${data.settings.signWindowStart}–${data.settings.signWindowEnd}（${data.settings.timezone}）。${jobs ? '下次任务：' + jobs : ''}`
      : '调度器未运行（可能已在配置中关闭）。';
  }

  /* ---------------------------------------------------------------- 审批 */

  function renderPending() {
    const box = $('pendingBox');
    if (!state.pending.length) {
      box.innerHTML = '<div class="muted">暂无待审批申请。</div>';
      return;
    }
    box.innerHTML = `<div class="table-scroll"><table>
      <thead><tr><th>学号</th><th>姓名</th><th>专业</th><th>手机</th><th>备注</th><th>申请时间</th><th>操作</th></tr></thead>
      <tbody>${state.pending.map((u) => `<tr>
        <td class="mono">${escapeHtml(u.studentId)}</td>
        <td>${escapeHtml(u.name || '-')}</td>
        <td>${escapeHtml(u.major || '-')}</td>
        <td>${escapeHtml(u.phone || '-')}</td>
        <td>${escapeHtml(u.remark || '-')}</td>
        <td class="muted nowrap">${escapeHtml(u.createdAt || '')}</td>
        <td class="nowrap">
          <button class="ok sm" data-approve="${escapeHtml(u.studentId)}">批准</button>
          <button class="danger sm" data-reject="${escapeHtml(u.studentId)}">拒绝</button>
        </td>
      </tr>`).join('')}</tbody></table></div>`;

    box.querySelectorAll('[data-approve]').forEach((btn) => {
      btn.addEventListener('click', () => act(`/admin/api/users/${btn.dataset.approve}/approve`, 'POST'));
    });
    box.querySelectorAll('[data-reject]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const reason = prompt('拒绝原因（会展示给该同学）：', '暂不开放') || '';
        act(`/admin/api/users/${btn.dataset.reject}/reject`, 'POST', { reason });
      });
    });
  }

  /* ---------------------------------------------------------------- 成员 */

  function credentialTag(user) {
    if (user.credentialMode === 'none') return tag('err', '无凭据');
    if (user.credentialMode === 'password') return tag('info', '密码模式');
    const days = user.tokenDaysLeft;
    const renew = user.canRenew ? ' · 可续期' : '';
    if (days === null || days === undefined) return tag('info', '会话' + renew);
    if (days <= 0) return user.canRenew ? tag('ok', '会话已过期·将自动续期') : tag('err', '会话已过期');
    if (days < 3) {
      return user.canRenew
        ? tag('ok', `会话剩 ${days} 天${renew}`)
        : tag('warn', `会话剩 ${days} 天${renew}`);
    }
    return tag('ok', `会话剩 ${Math.floor(days)} 天${renew}`);
  }

  function renderMembers(users) {
    $('memberCount').textContent = users.length;
    const box = $('membersBox');
    if (!users.length) {
      box.innerHTML = '<div class="muted">暂无已批准成员。</div>';
      return;
    }

    const needRecollect = users.filter((u) => u.needsRecollect);
    const notice = needRecollect.length
      ? `<div class="alert warn">以下 ${needRecollect.length} 位同学<b>没有续期凭据且会话即将/已经过期</b>，
           需要重新采集：<b>${needRecollect.map((u) => escapeHtml(u.studentId)).join('、')}</b>
           <br><span class="mono">python tools/token_catcher.py 学号</span>
           <br>提示：采集时让同学<b>走一次完整的重新登录</b>（先退出登录再登），
           这样才能拿到 refresh_token，之后就不用反复采集了。</div>`
      : '';

    const renewable = users.filter((u) => u.canRenew).length;
    const summary = users.length
      ? `<p class="sub">共 ${users.length} 位，其中 <b>${renewable}</b> 位具备自动续期能力
         ${renewable === users.length ? '（都无需再次采集）' : ''}。</p>`
      : '';

    box.innerHTML = notice + summary + `<div class="table-scroll"><table>
      <thead><tr>
        <th>学号</th><th>姓名</th><th>状态</th><th>凭据</th><th>签到时刻</th><th>宿舍</th>
        <th>最近错误</th><th>操作</th>
      </tr></thead>
      <tbody>${users.map((u) => `<tr>
        <td class="mono">${escapeHtml(u.studentId)}</td>
        <td>${escapeHtml(u.name || '-')}</td>
        <td>${approvalTag(u)} ${statusTag(u)}</td>
        <td>${credentialTag(u)}</td>
        <td class="nowrap">${escapeHtml(u.planSignTime || '-')}</td>
        <td class="nowrap">${escapeHtml((u.dormName || '-') + ' ' + (u.roomNo || ''))}</td>
        <td class="muted" style="max-width:200px">${escapeHtml((u.lastError || '').slice(0, 80))}</td>
        <td class="nowrap">
          <button class="ghost sm" data-sign="${escapeHtml(u.studentId)}">手动签到</button>
          <button class="ghost sm" data-toggle="${escapeHtml(u.studentId)}" data-enabled="${u.enabled ? '1' : '0'}">${u.enabled ? '停用' : '启用'}</button>
          <button class="ghost sm" data-reset="${escapeHtml(u.studentId)}">重置口令</button>
          <button class="ghost sm" data-revoke="${escapeHtml(u.studentId)}">撤回</button>
          <button class="danger sm" data-del="${escapeHtml(u.studentId)}">删除</button>
        </td>
      </tr>`).join('')}</tbody></table></div>`;

    const bind = (attr, handler) => {
      box.querySelectorAll(`[data-${attr}]`).forEach((btn) => {
        btn.addEventListener('click', () => handler(btn.dataset[attr], btn));
      });
    };

    bind('sign', (sid) => act(`/admin/api/users/${sid}/sign`, 'POST'));
    bind('toggle', (sid, btn) => act(`/admin/api/users/${sid}/enabled`, 'POST', { enabled: btn.dataset.enabled !== '1' }));
    bind('reset', async (sid) => {
      if (!confirm(`确认重置 ${sid} 的查询口令？`)) return;
      const data = await act(`/admin/api/users/${sid}/reset-password`, 'POST');
      if (data) alert(data.message);
    });
    bind('revoke', (sid) => {
      const reason = prompt('撤回原因：', '管理员撤回授权') || '';
      act(`/admin/api/users/${sid}/revoke`, 'POST', { reason });
    });
    bind('del', (sid) => {
      if (!confirm(`确认删除 ${sid}？该操作会同时删除其签到记录，不可恢复。`)) return;
      act(`/admin/api/users/${sid}`, 'DELETE');
    });
  }

  /* ---------------------------------------------------------------- 动作 */

  const actionAlert = () => $('actionAlert');

  async function act(path, method = 'POST', body) {
    try {
      const data = await api(path, {
        method,
        body: body === undefined && method !== 'DELETE' ? JSON.stringify({}) : (body ? JSON.stringify(body) : undefined),
      });
      alertBox(actionAlert(), 'ok', escapeHtml(data.message || '操作成功'));
      await refreshAll();
      return data;
    } catch (e) {
      alertBox(actionAlert(), 'err', escapeHtml(e.message));
      return null;
    }
  }

  $('btnRandomize').addEventListener('click', () => act('/admin/api/randomize', 'POST', {}));
  $('btnRetry').addEventListener('click', () => act('/admin/api/retry-failed', 'POST', { days: 1 }));
  $('btnRunToday').addEventListener('click', () => {
    if (!confirm('立即为所有符合条件的成员执行签到？')) return;
    act('/admin/api/run-daily', 'POST', {});
  });

  $('btnCheckDate').addEventListener('click', async () => {
    const date = $('checkDate').value;
    const box = $('checkDateResult');
    try {
      const data = await api('/admin/api/check-calendar', {
        method: 'POST',
        body: JSON.stringify({ date }),
      });
      box.innerHTML = `<div class="alert ${data.shouldSign ? 'ok' : 'warn'}">
        <b>${data.date}</b>：${data.shouldSign ? '会执行自动签到' : '不会执行自动签到'} — ${escapeHtml(data.reason)}
        ${data.term ? '<br>学期：' + escapeHtml(data.term.name) : ''}
        ${data.holiday ? '<br>假期：' + escapeHtml(data.holiday.name) : ''}
      </div>`;
    } catch (e) {
      box.innerHTML = `<div class="alert err">${escapeHtml(e.message)}</div>`;
    }
  });

  /* ---------------------------------------------------------------- 校历 */

  async function loadTerms() {
    const data = await api('/admin/api/terms');
    const box = $('termsBox');
    if (!data.terms.length) {
      box.innerHTML = '<div class="alert warn">尚未配置任何学期。'
        + '系统**仍会**按学校任务的打卡期间自动签到（寒假留校等场景也覆盖），'
        + '此处配置只影响日历看板的着色与「假期无需签到」提示。</div>';
      return;
    }
    box.innerHTML = `<div class="table-scroll"><table>
      <thead><tr><th>学期</th><th>开始</th><th>结束</th><th>天数</th><th>备注</th><th></th></tr></thead>
      <tbody>${data.terms.map((t) => `<tr>
        <td>${escapeHtml(t.name)}</td>
        <td class="nowrap">${escapeHtml(t.start_date)}</td>
        <td class="nowrap">${escapeHtml(t.end_date)}</td>
        <td class="muted nowrap">${t.days || 0} 天</td>
        <td class="muted">${escapeHtml(t.note || '')}</td>
        <td><button class="danger sm" data-delterm="${t.id}">删除</button></td>
      </tr>`).join('')}</tbody></table></div>`;
    box.querySelectorAll('[data-delterm]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        if (!confirm('确认删除该学期？')) return;
        await act(`/admin/api/terms/${btn.dataset.delterm}`, 'DELETE');
        await loadTerms();
      });
    });
  }

  $('termForm').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const data = await act('/admin/api/terms', 'POST', {
      name: $('termName').value.trim(),
      note: $('termNote').value.trim(),
      startDate: $('termStart').value,
      endDate: $('termEnd').value,
    });
    if (data) { $('termForm').reset(); await loadTerms(); }
  });

  async function loadHolidays() {
    const data = await api('/admin/api/holidays');
    const box = $('holidaysBox');
    if (!data.holidays.length) {
      box.innerHTML = '<div class="muted">尚未配置假期区间。</div>';
      return;
    }
    const kindName = { vacation: '假期', holiday: '节假日', makeup: '调休上课' };
    box.innerHTML = `<div class="table-scroll"><table>
      <thead><tr><th>名称</th><th>类型</th><th>开始</th><th>结束</th><th>天数</th><th></th></tr></thead>
      <tbody>${data.holidays.map((h) => `<tr>
        <td>${escapeHtml(h.name)}</td>
        <td>${tag(h.kind === 'makeup' ? 'info' : 'skip', kindName[h.kind] || h.kind)}</td>
        <td class="nowrap">${escapeHtml(h.start_date)}</td>
        <td class="nowrap">${escapeHtml(h.end_date)}</td>
        <td class="muted nowrap">${h.days || 0} 天</td>
        <td><button class="danger sm" data-delhol="${h.id}">删除</button></td>
      </tr>`).join('')}</tbody></table></div>`;
    box.querySelectorAll('[data-delhol]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        if (!confirm('确认删除？')) return;
        await act(`/admin/api/holidays/${btn.dataset.delhol}`, 'DELETE');
        await loadHolidays();
      });
    });
  }

  $('holidayForm').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const data = await act('/admin/api/holidays', 'POST', {
      name: $('holidayName').value.trim(),
      kind: $('holidayKind').value,
      startDate: $('holidayStart').value,
      endDate: $('holidayEnd').value,
    });
    if (data) { $('holidayForm').reset(); await loadHolidays(); }
  });

  /* ---------------------------------------------------------------- 日志 */

  async function loadLogs() {
    const filter = $('logFilter').value.trim();
    const url = '/admin/api/logs?limit=300' + (filter ? '&studentId=' + encodeURIComponent(filter) : '');
    const data = await api(url);
    state.logs = data.logs || [];
    const box = $('logsBox');
    if (!state.logs.length) { box.innerHTML = '<div class="muted">暂无日志</div>'; return; }
    box.innerHTML = state.logs.map((l) => `<div class="log-item ${escapeHtml(l.level)}">
      <span class="t">${escapeHtml(l.created_at)}</span>
      <span class="a">${escapeHtml(l.action)}</span>
      <span class="m">${escapeHtml(l.student_id ? '[' + l.student_id + '] ' : '')}${escapeHtml(l.message)}</span>
    </div>`).join('');
  }

  $('btnReloadLogs').addEventListener('click', loadLogs);
  $('btnFilterLogs').addEventListener('click', loadLogs);

  bootstrap();
})();
