/* 用户端交互逻辑（原生 JS，无需构建） */
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const state = {
    config: {},
    overview: null,
    calendar: { year: 0, month: 0 },
    view: 'login',
  };

  /* ---------------------------------------------------------------- 工具 */

  function alertBox(el, kind, message) {
    el.className = 'alert ' + kind;
    el.innerHTML = message;
    el.classList.remove('hidden');
  }
  function hide(el) { el.classList.add('hidden'); }

  async function api(path, options = {}) {
    const res = await fetch(path, {
      credentials: 'same-origin',
      headers: options.body ? { 'Content-Type': 'application/json' } : {},
      ...options,
    });
    let data = {};
    try { data = await res.json(); } catch (e) { data = {}; }
    if (!res.ok || data.success === false) {
      const err = new Error(data.message || data.detail || `请求失败 (${res.status})`);
      err.data = data;
      err.status = res.status;
      throw err;
    }
    return data;
  }

  function show(view) {
    state.view = view;
    ['loginCard', 'applyCard', 'bindCard', 'dashCard'].forEach((id) => {
      $(id).classList.toggle('hidden', id !== view + 'Card');
    });
  }

  function tagFor(status, statusName) {
    const map = {
      success: 'ok', failed: 'err', pending: 'warn',
      skipped: 'skip', unknown: 'skip', future: 'skip', no_task: 'skip',
    };
    return `<span class="tag ${map[status] || 'skip'}">${statusName || status}</span>`;
  }

  /* ---------------------------------------------------------------- 会话 */

  async function bootstrap() {
    try {
      const data = await api('/api/me');
      state.config = data.config || {};
      $('siteName').textContent = state.config.siteName || '学工自动签到';
      if (data.loggedIn && data.overview) {
        state.overview = data.overview;
        await enterDashboard();
      } else {
        show('login');
      }
    } catch (e) {
      show('login');
    }
  }

  /* ---------------------------------------------------------------- 登录 */

  $('loginForm').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const box = $('loginAlert');
    const btn = $('loginBtn');
    btn.disabled = true;
    try {
      const data = await api('/api/login', {
        method: 'POST',
        body: JSON.stringify({
          studentId: $('loginStudentId').value.trim(),
          queryPassword: $('loginPassword').value,
        }),
      });
      state.overview = data.overview;
      hide(box);
      await enterDashboard();
    } catch (e) {
      alertBox(box, 'err', e.message);
    } finally {
      btn.disabled = false;
    }
  });

  async function enterDashboard() {
    show('dash');
    const o = state.overview || {};
    $('dashWho').textContent = `${o.user?.name || ''} ${o.user?.studentId || ''} · ${o.planSignTime || '--:--'} 自动签到`;
    renderToday(o);
    const now = new Date();
    state.calendar.year = now.getFullYear();
    state.calendar.month = now.getMonth() + 1;
    await loadCalendar(false);
  }

  function renderToday(o) {
    const today = o.today || {};
    const rec = today.record || {};
    const parts = [];
    if (!today.shouldSign) {
      parts.push(`<div class="alert warn">今天不需要自动签到：${today.reason || ''}</div>`);
    }
    parts.push(`
      <div class="grid four">
        <div class="stat"><div class="k">今天</div><div class="v" style="font-size:18px">${today.date || ''}</div></div>
        <div class="stat"><div class="k">今日状态</div><div class="v" style="font-size:18px">${tagFor(rec.status || (today.shouldSign ? 'pending' : 'skipped'), rec.statusName || (today.shouldSign ? '待签到' : '无需签到'))}</div></div>
        <div class="stat"><div class="k">自动签到时刻</div><div class="v" style="font-size:18px">${o.planSignTime || '--:--'}</div></div>
        <div class="stat"><div class="k">宿舍</div><div class="v" style="font-size:18px">${o.task?.dormName || '-'} ${o.task?.roomNo || ''}</div></div>
      </div>
    `);
    if (rec.message) {
      parts.push(`<p class="muted mt">最近一次结果：${rec.message}${rec.signTime ? '（' + rec.signTime + '）' : ''}</p>`);
    }
    $('todayPanel').innerHTML = parts.join('');
  }

  /* ---------------------------------------------------------------- 申请 */

  $('showApply').addEventListener('click', () => { show('apply'); hide($('applyAlert')); });
  $('applyBack').addEventListener('click', () => show('login'));
  $('bindBack').addEventListener('click', () => show('login'));

  $('applyForm').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const box = $('applyAlert');
    const btn = $('applyBtn');
    btn.disabled = true;
    try {
      const data = await api('/api/apply', {
        method: 'POST',
        body: JSON.stringify({
          studentId: $('applyStudentId').value.trim(),
          name: $('applyName').value.trim(),
          phone: $('applyPhone').value.trim(),
          major: $('applyMajor').value.trim(),
          remark: $('applyRemark').value.trim(),
        }),
      });
      alertBox(box, 'ok', `${data.message}。批准后回到本页，点击“首次绑定学校账号”完成设置。`);
      $('applyForm').reset();
      await queryStatus();
    } catch (e) {
      alertBox(box, 'err', e.message);
    } finally {
      btn.disabled = false;
    }
  });

  $('queryStatus').addEventListener('click', queryStatus);

  async function queryStatus() {
    const sid = $('applyStudentId').value.trim() || $('statusStudentId').value.trim();
    const box = $('applyAlert');
    if (!sid) { alertBox(box, 'warn', '请先填写学号'); return; }
    $('statusStudentId').value = sid;
    try {
      const data = await api('/api/apply/status?studentId=' + encodeURIComponent(sid));
      const kind = data.state === 'approved' ? 'ok' : data.state === 'rejected' ? 'err' : 'info';
      alertBox(box, kind, `<b>${sid}</b>：${data.message}`);
      if (data.state === 'approved') {
        $('bindStudentId').value = sid;
        $('showBind').classList.remove('hidden');
      } else {
        $('showBind').classList.add('hidden');
      }
    } catch (e) {
      alertBox(box, 'err', e.message);
    }
  }

  $('showBind').addEventListener('click', async () => {
    $('bindStudentId').value = $('applyStudentId').value.trim() || $('statusStudentId').value.trim();
    show('bind');
    hide($('bindAlert'));
    await loadCaptcha();
  });

  /* ---------------------------------------------------------------- 绑定 */

  async function loadCaptcha() {
    const box = $('captchaBox');
    try {
      const data = await api('/api/captcha');
      if (data.image) {
        box.innerHTML = `<img id="captchaImg" src="${data.image}" alt="验证码" title="点击刷新">`;
        box.dataset.key = data.captchaKey || '';
        $('captchaImg').addEventListener('click', loadCaptcha);
        $('bindAlert').classList.add('hidden');
      } else {
        box.innerHTML = `<div class="ph" id="captchaPh">点击重试</div>`;
        box.dataset.key = '';
        $('captchaPh').addEventListener('click', loadCaptcha);
        if (data.message) alertBox($('bindAlert'), 'warn', data.message);
      }
    } catch (e) {
      box.innerHTML = `<div class="ph">不可用</div>`;
      box.dataset.key = '';
    }
  }
  $('refreshCaptcha').addEventListener('click', loadCaptcha);

  $('bindForm').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const box = $('bindAlert');
    const btn = $('bindBtn');
    btn.disabled = true;
    try {
      const data = await api('/api/bind', {
        method: 'POST',
        body: JSON.stringify({
          studentId: $('bindStudentId').value.trim(),
          password: $('bindPassword').value,
          queryPassword: $('bindQueryPassword').value,
          captchaKey: $('captchaBox').dataset.key || '',
          captchaCode: $('bindCaptchaCode').value.trim(),
        }),
      });
      state.overview = null;
      alertBox(box, 'ok', data.message + ' 正在进入看板…');
      $('bindForm').reset();
      const me = await api('/api/me');
      state.overview = me.overview || null;
      if (state.overview) await enterDashboard();
    } catch (e) {
      alertBox(box, 'err', e.message);
      await loadCaptcha();
    } finally {
      btn.disabled = false;
    }
  });

  /* ---------------------------------------------------------------- 日历 */

  const MONTH_NAMES = ['1 月', '2 月', '3 月', '4 月', '5 月', '6 月',
    '7 月', '8 月', '9 月', '10 月', '11 月', '12 月'];

  async function loadCalendar(sync) {
    const { year, month } = state.calendar;
    $('calTitle').textContent = `${year} 年 ${MONTH_NAMES[month - 1]}`;
    $('calGrid').innerHTML = '<div class="muted">加载中…</div>';
    try {
      const data = await api(`/api/calendar?year=${year}&month=${month}${sync ? '&sync=1' : ''}`);
      renderCalendar(data);
    } catch (e) {
      $('calGrid').innerHTML = `<div class="alert err">${e.message}</div>`;
    }
  }

  function renderCalendar(data) {
    const dows = ['一', '二', '三', '四', '五', '六', '日'];
    const cells = dows.map((d) => `<div class="cal-dow">${d}</div>`);

    const days = data.days || [];
    if (days.length) {
      const firstDow = (days[0].weekday + 7) % 7; // 周一为 0
      for (let i = 0; i < firstDow; i++) cells.push('<div class="cal-cell empty"></div>');
    }
    days.forEach((d) => {
      const cls = ['cal-cell', d.status, d.isToday ? 'today' : ''].filter(Boolean).join(' ');
      const mark = d.status === 'success' ? '✓'
        : d.status === 'failed' ? '✕'
        : d.status === 'skipped' ? '–'
        : d.status === 'pending' ? '·' : '';
      const title = `${d.date}｜${d.statusName}${d.message ? '｜' + d.message : ''}`;
      cells.push(`<div class="${cls}" title="${escapeHtml(title)}">
        <span class="d">${d.day}</span><span class="m">${mark}</span></div>`);
    });
    $('calGrid').innerHTML = cells.join('');

    const s = data.summary || {};
    $('calSummary').innerHTML = `
      <span>应签到 <b>${s.planned || 0}</b> 天</span>
      <span class="tag ok">成功 ${s.success || 0}</span>
      <span class="tag err">失败 ${s.failed || 0}</span>
      <span class="tag skip">无需 ${s.skipped || 0}</span>
      <span class="tag warn">待确认 ${s.unknown || 0}</span>
    `;
  }

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
  }

  $('calPrev').addEventListener('click', () => {
    state.calendar.month -= 1;
    if (state.calendar.month < 1) { state.calendar.month = 12; state.calendar.year -= 1; }
    loadCalendar(false);
  });
  $('calNext').addEventListener('click', () => {
    state.calendar.month += 1;
    if (state.calendar.month > 12) { state.calendar.month = 1; state.calendar.year += 1; }
    loadCalendar(false);
  });
  $('calToday').addEventListener('click', () => {
    const now = new Date();
    state.calendar.year = now.getFullYear();
    state.calendar.month = now.getMonth() + 1;
    loadCalendar(false);
  });
  $('calSync').addEventListener('click', () => loadCalendar(true));

  /* ---------------------------------------------------------------- 记录 */

  $('loadRecords').addEventListener('click', async () => {
    const box = $('recordsBox');
    box.innerHTML = '<div class="muted">加载中…</div>';
    try {
      const data = await api('/api/records?limit=90');
      const rows = data.records || [];
      if (!rows.length) { box.innerHTML = '<div class="muted">暂无记录</div>'; return; }
      box.innerHTML = `<div class="table-scroll"><table>
        <thead><tr><th>日期</th><th>状态</th><th>说明</th><th>时间</th></tr></thead>
        <tbody>${rows.map((r) => `<tr>
          <td class="nowrap">${r.sign_date}</td>
          <td>${tagFor(r.status, tagName(r.status))}</td>
          <td>${escapeHtml(r.message || '')}</td>
          <td class="nowrap muted">${r.sign_time || ''}</td>
        </tr>`).join('')}</tbody></table></div>`;
    } catch (e) {
      box.innerHTML = `<div class="alert err">${e.message}</div>`;
    }
  });

  function tagName(status) {
    return { success: '已签到', failed: '失败', pending: '待签到', skipped: '无需', unknown: '待确认', no_task: '无任务' }[status] || status;
  }

  /* ---------------------------------------------------------------- 口令 */

  $('logoutBtn').addEventListener('click', async () => {
    await api('/api/logout', { method: 'POST' });
    state.overview = null;
    show('login');
  });

  bootstrap();
})();
