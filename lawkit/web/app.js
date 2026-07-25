/* 法規整理 App
 *
 * 資料來源有兩種，會自動切換：
 *   1. 本機伺服器 API（law serve）── 可讀寫，資料存在 D:\Vivian_Law\lawkit.db
 *   2. 離線資料包（IndexedDB 內的 bundle）── 沒有網路或伺服器沒開時，仍可瀏覽與檢索
 *
 * 離線時所做的標籤／筆記／收藏會排入待送佇列，連上伺服器後自動補送。
 */

const APP_VERSION = '0.1.0';

// ------------------------------------------------------------------ 小工具
const $ = (selector, scope = document) => scope.querySelector(selector);

const escapeHtml = (value) =>
  String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));

const toast = (message, ms = 2200) => {
  const node = $('#toast');
  node.textContent = message;
  node.hidden = false;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { node.hidden = true; }, ms);
};

const formatTimestamp = (value) => {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 16).replace('T', ' ');
  const pad = (n) => String(n).padStart(2, '0');
  return `${date.getFullYear()}/${pad(date.getMonth() + 1)}/${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
};

const settings = {
  data: { theme: 'auto', readingSize: 17, autoBundle: true },
  load() {
    try {
      Object.assign(this.data, JSON.parse(localStorage.getItem('lawkit.settings') || '{}'));
    } catch { /* 忽略毀損的設定 */ }
    return this.data;
  },
  save(changes) {
    Object.assign(this.data, changes);
    localStorage.setItem('lawkit.settings', JSON.stringify(this.data));
    applySettings();
  },
};

function applySettings() {
  const { theme, readingSize } = settings.data;
  const resolved = theme === 'auto'
    ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
    : theme;
  document.documentElement.dataset.theme = resolved;
  document.documentElement.style.setProperty('--reading-size', `${readingSize}px`);
}

const CHINESE_DIGITS = { 零: 0, 〇: 0, 一: 1, 二: 2, 兩: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9 };
const CHINESE_UNITS = { 十: 10, 百: 100, 千: 1000 };

function chineseToInt(text) {
  const raw = String(text).replace(/[\s\u3000]/g, '');
  if (/^\d+$/.test(raw)) return Number(raw);
  let total = 0;
  let digit = null;
  for (const ch of raw) {
    if (ch in CHINESE_DIGITS) digit = CHINESE_DIGITS[ch];
    else if (ch in CHINESE_UNITS) { total += (digit ?? 1) * CHINESE_UNITS[ch]; digit = null; }
    else if (/\d/.test(ch)) digit = Number(ch);
    else return NaN;
  }
  return total + (digit ?? 0);
}

// ------------------------------------------------------------------ IndexedDB
const db = {
  handle: null,
  async open() {
    if (this.handle) return this.handle;
    this.handle = await new Promise((resolve, reject) => {
      const request = indexedDB.open('lawkit', 1);
      request.onupgradeneeded = () => {
        const database = request.result;
        if (!database.objectStoreNames.contains('cache')) database.createObjectStore('cache');
        if (!database.objectStoreNames.contains('pending')) {
          database.createObjectStore('pending', { keyPath: 'id', autoIncrement: true });
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    return this.handle;
  },
  async run(storeName, mode, action) {
    const database = await this.open();
    return new Promise((resolve, reject) => {
      const transaction = database.transaction(storeName, mode);
      const request = action(transaction.objectStore(storeName));
      transaction.onerror = () => reject(transaction.error);
      if (request) request.onsuccess = () => resolve(request.result);
      else transaction.oncomplete = () => resolve();
    });
  },
  get(key) { return this.run('cache', 'readonly', (store) => store.get(key)); },
  put(key, value) { return this.run('cache', 'readwrite', (store) => store.put(value, key)); },
  queue(entry) { return this.run('pending', 'readwrite', (store) => store.add(entry)); },
  pending() { return this.run('pending', 'readonly', (store) => store.getAll()); },
  unqueue(id) { return this.run('pending', 'readwrite', (store) => store.delete(id)); },
};

// ------------------------------------------------------------------ 資料層
const state = {
  online: true,
  meta: null,
  bundle: null,
  bundleAt: null,
  laws: [],
  history: [],
};

class OfflineError extends Error {}

async function request(path, options = {}) {
  if (navigator.onLine === false) {
    setOnline(false);
    throw new OfflineError('裝置目前離線');
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch(path, {
      ...options,
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    });
    // Service Worker 在離線時會回 503，屬於「連不上」而非伺服器錯誤
    if (response.status === 503) {
      setOnline(false);
      throw new OfflineError('目前無法連上伺服器');
    }
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `伺服器回應 ${response.status}`);
    setOnline(true);
    return payload;
  } catch (error) {
    if (error.name === 'AbortError' || error instanceof TypeError) {
      setOnline(false);
      throw new OfflineError('目前無法連上伺服器');
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

function setOnline(online) {
  if (state.online === online) return;
  state.online = online;
  $('#offline-badge').hidden = online;
  if (online) flushPending();
}

// ---- 離線資料包 -----------------------------------------------------------
async function loadBundleFromCache() {
  const cached = await db.get('bundle').catch(() => null);
  if (cached && cached.laws) {
    state.bundle = cached;
    state.bundleAt = cached.generated_at;
    indexBundle(cached);
  }
  return state.bundle;
}

async function refreshBundle({ silent = true } = {}) {
  let bundle = null;
  try {
    bundle = await request('/api/bundle');
  } catch {
    try {
      const response = await fetch('data/bundle.json', { cache: 'no-cache' });
      if (response.ok) bundle = await response.json();
    } catch { /* 純靜態部署時才會走到這裡 */ }
  }
  if (!bundle || !bundle.laws) {
    if (!silent) toast('無法取得離線資料');
    return null;
  }
  state.bundle = bundle;
  state.bundleAt = bundle.generated_at;
  indexBundle(bundle);
  await db.put('bundle', bundle).catch(() => {});
  if (!silent) toast(`離線資料已更新（${bundle.laws.length} 部法規）`);
  return bundle;
}

function indexBundle(bundle) {
  bundle._articles = new Map();
  bundle._incoming = new Map();
  for (const law of bundle.laws) {
    for (const article of law.articles) {
      article.law_id = law.id;
      article.law_name = law.name;
      bundle._articles.set(article.id, article);
    }
  }
  for (const law of bundle.laws) {
    for (const article of law.articles) {
      for (const ref of article.refs || []) {
        if (!ref.to_article_id) continue;
        const list = bundle._incoming.get(ref.to_article_id) || [];
        list.push({ ...ref, from_id: article.id, from_label: article.label, from_law_name: law.name });
        bundle._incoming.set(ref.to_article_id, list);
      }
    }
  }
}

function makeSnippet(text, term, width = 60) {
  const flat = text.replace(/\n/g, ' ');
  const position = flat.indexOf(term);
  if (position < 0) return flat.slice(0, width * 2);
  const start = Math.max(0, position - width / 2);
  const end = Math.min(flat.length, position + term.length + width);
  return `${start > 0 ? '…' : ''}${flat.slice(start, end)}${end < flat.length ? '…' : ''}`;
}

// ---- 統一的資料 API -----------------------------------------------------
const api = {
  async meta() {
    try {
      const meta = await request('/api/meta');
      state.meta = meta;
      await db.put('meta', meta).catch(() => {});
      return meta;
    } catch (error) {
      if (!(error instanceof OfflineError)) throw error;
      state.meta = (await db.get('meta').catch(() => null)) || null;
      return state.meta;
    }
  },

  async laws() {
    try {
      state.laws = await request('/api/laws');
      return state.laws;
    } catch (error) {
      if (!(error instanceof OfflineError)) throw error;
      const bundle = state.bundle || (await loadBundleFromCache()) || (await refreshBundle());
      state.laws = (bundle?.laws || []).map((law) => ({
        ...law,
        article_count: law.articles.length,
        imported_at: bundle.generated_at,
      }));
      return state.laws;
    }
  },

  async law(id) {
    try {
      return await request(`/api/laws/${id}`);
    } catch (error) {
      if (!(error instanceof OfflineError)) throw error;
      const bundle = state.bundle || (await loadBundleFromCache());
      const law = bundle?.laws.find((item) => String(item.id) === String(id));
      if (!law) throw new Error('離線資料中找不到這部法規');
      return { ...law, tree: buildOfflineTree(law), versions: [], stats: law.stats || {} };
    }
  },

  async article(id) {
    try {
      return await request(`/api/articles/${id}`);
    } catch (error) {
      if (!(error instanceof OfflineError)) throw error;
      const bundle = state.bundle || (await loadBundleFromCache());
      const article = bundle?._articles.get(Number(id));
      if (!article) throw new Error('離線資料中找不到這一條');
      return {
        ...article,
        note: article.note ? { body: article.note } : null,
        tags: (article.tags || []).map((name) => ({ name, color: '' })),
        bookmarked: (bundle.bookmarks || []).includes(article.id),
        outgoing: (article.refs || []).map((ref) => ({ ...ref, target_law_name: ref.target_law })),
        incoming: bundle._incoming.get(article.id) || [],
      };
    }
  },

  async search(query, { lawId = '', limit = 50 } = {}) {
    const params = new URLSearchParams({ q: query, limit: String(limit) });
    if (lawId) params.set('law', lawId);
    try {
      return await request(`/api/search?${params}`);
    } catch (error) {
      if (!(error instanceof OfflineError)) throw error;
      const bundle = state.bundle || (await loadBundleFromCache());
      const hits = [];
      for (const law of bundle?.laws || []) {
        if (lawId && String(law.id) !== String(lawId)) continue;
        for (const article of law.articles) {
          if (article.text.includes(query) || article.label.includes(query)) {
            hits.push({
              article_id: article.id,
              law_id: law.id,
              law_name: law.name,
              law_version: law.version_label || '',
              label: article.label,
              division_path: article.division_path,
              snippet: makeSnippet(article.text, query),
            });
            if (hits.length >= limit) break;
          }
        }
      }
      return { query, count: hits.length, hits };
    }
  },

  async history() {
    try { return await request('/api/history?limit=8'); } catch { return []; }
  },

  async tags() {
    try {
      return await request('/api/tags');
    } catch (error) {
      if (!(error instanceof OfflineError)) throw error;
      return (state.bundle?.tags || []).map((tag) => ({ ...tag }));
    }
  },

  async bookmarks() {
    try { return await request('/api/bookmarks'); } catch { return []; }
  },

  async notes() {
    try { return await request('/api/notes'); } catch { return []; }
  },

  async addTag(articleId, name) {
    return mutate(`/api/articles/${articleId}/tags`, 'POST', { name }, { type: 'addTag', articleId, name });
  },

  async removeTag(articleId, name) {
    return mutate(
      `/api/articles/${articleId}/tags/${encodeURIComponent(name)}`, 'DELETE', null,
      { type: 'removeTag', articleId, name },
    );
  },

  async setNote(articleId, body) {
    return mutate(`/api/articles/${articleId}/note`, 'PUT', { body }, { type: 'setNote', articleId, body });
  },

  async toggleBookmark(articleId) {
    return mutate(`/api/articles/${articleId}/bookmark`, 'POST', {}, { type: 'bookmark', articleId });
  },

  async importText(payload) {
    return request('/api/import', { method: 'POST', body: JSON.stringify(payload) });
  },

  async diff(fromId, toId) {
    return request(`/api/diff?from=${fromId}&to=${toId}`);
  },

  async exportAs(format, lawId = '') {
    const params = new URLSearchParams({ format });
    if (lawId) params.set('law', lawId);
    return request(`/api/export?${params}`);
  },
};

async function mutate(path, method, body, offlineEntry) {
  try {
    return await request(path, { method, body: body ? JSON.stringify(body) : undefined });
  } catch (error) {
    if (!(error instanceof OfflineError)) throw error;
    await db.queue({ ...offlineEntry, path, method, body, at: Date.now() });
    applyOfflineMutation(offlineEntry);
    toast('已離線儲存，連線後自動同步');
    return { offline: true };
  }
}

function applyOfflineMutation(entry) {
  const article = state.bundle?._articles.get(Number(entry.articleId));
  if (!article) return;
  if (entry.type === 'addTag') article.tags = [...new Set([...(article.tags || []), entry.name])];
  if (entry.type === 'removeTag') article.tags = (article.tags || []).filter((name) => name !== entry.name);
  if (entry.type === 'setNote') article.note = entry.body;
  if (entry.type === 'bookmark') {
    const marks = state.bundle.bookmarks || [];
    state.bundle.bookmarks = marks.includes(article.id)
      ? marks.filter((id) => id !== article.id)
      : [...marks, article.id];
  }
  db.put('bundle', state.bundle).catch(() => {});
}

async function flushPending() {
  const entries = await db.pending().catch(() => []);
  if (!entries.length) return;
  let sent = 0;
  for (const entry of entries) {
    try {
      await request(entry.path, {
        method: entry.method,
        body: entry.body ? JSON.stringify(entry.body) : undefined,
      });
      await db.unqueue(entry.id);
      sent += 1;
    } catch {
      return; // 仍然連不上，下次再試
    }
  }
  if (sent) toast(`已同步 ${sent} 筆離線變更`);
}

function buildOfflineTree(law) {
  const divisions = new Map(law.divisions.map((division) => [division.seq, { ...division, type: 'division', children: [] }]));
  const roots = [];
  for (const division of divisions.values()) {
    const parent = division.parent_seq != null ? divisions.get(division.parent_seq) : null;
    (parent ? parent.children : roots).push(division);
  }
  const loose = [];
  for (const article of law.articles) {
    const entry = {
      type: 'article',
      id: article.id,
      label: article.label,
      label_chinese: article.label_chinese,
      is_deleted: article.is_deleted,
      preview: article.text.split('\n')[0].slice(0, 60),
    };
    const owner = article.division_seq != null ? divisions.get(article.division_seq) : null;
    (owner ? owner.children : loose).push(entry);
  }
  return [...roots, ...loose];
}

// ------------------------------------------------------------------ 畫面
const view = $('#view');

function setHeader(title, subtitle, { back = false } = {}) {
  $('#page-title').textContent = title;
  $('#page-subtitle').textContent = subtitle || '';
  $('#back-button').hidden = !back;
}

function setActiveTab(tab) {
  for (const item of document.querySelectorAll('.tabbar__item')) {
    if (item.dataset.tab === tab) item.setAttribute('aria-current', 'page');
    else item.removeAttribute('aria-current');
  }
}

function render(html) {
  view.onclick = null; // 換頁時清掉上一頁註冊的委派事件
  view.innerHTML = html;
  view.scrollTop = 0;
  window.scrollTo({ top: 0 });
}

const loading = () => render('<div class="loading">載入中…</div>');

function lawCard(law) {
  const meta = [
    law.version_label || '未註明版本',
    `${law.article_count ?? law.articles?.length ?? 0} 條`,
    law.category || '',
  ].filter(Boolean);
  return `
    <a class="card card--tap" href="#/law/${law.id}">
      <p class="law-card__name">${escapeHtml(law.name)}</p>
      <div class="law-card__meta">${meta.map((item) => `<span>${escapeHtml(item)}</span>`).join('')}</div>
    </a>`;
}

// ---- 首頁 ---------------------------------------------------------------
async function viewHome() {
  setHeader('法規整理', state.meta?.paths?.['資料根目錄'] || '整理你的法規知識庫');
  setActiveTab('home');
  loading();

  const [laws, bookmarks] = await Promise.all([api.laws(), api.bookmarks()]);
  const stats = state.meta?.stats;

  const statCards = stats ? `
    <div class="stat-grid">
      <div class="stat"><div class="stat__value">${stats.laws}</div><div class="stat__label">法規</div></div>
      <div class="stat"><div class="stat__value">${stats.articles}</div><div class="stat__label">條文</div></div>
      <div class="stat"><div class="stat__value">${stats.references}</div><div class="stat__label">交互參照</div></div>
      <div class="stat"><div class="stat__value">${stats.notes}</div><div class="stat__label">筆記</div></div>
    </div>` : '';

  const bookmarkList = bookmarks.length ? `
    <div class="section-title"><span>收藏</span><a href="#/organize">全部</a></div>
    <div class="list">
      ${bookmarks.slice(0, 4).map((row) => `
        <a class="card card--tap" href="#/article/${row.id}">
          <div class="hit__head"><span class="hit__law">${escapeHtml(row.law_name)}</span><span class="hit__label">${escapeHtml(row.label)}</span></div>
          <p class="hit__snippet">${escapeHtml(row.text.split('\n')[0].slice(0, 70))}</p>
        </a>`).join('')}
    </div>` : '';

  render(`
    ${statCards}
    ${bookmarkList}
    <div class="section-title"><span>法規清單</span><a href="#/import">＋ 匯入</a></div>
    ${laws.length
      ? `<div class="list">${laws.map(lawCard).join('')}</div>`
      : `<div class="card"><p>還沒有任何法規。</p>
         <p class="hit__snippet">用「匯入」把法規條文貼進來，或在電腦上執行
         <code>law import 檔案.txt</code>。</p>
         <a class="button button--block" href="#/import" style="margin-top:12px">開始匯入</a></div>`}
  `);
}

// ---- 法規（章節樹） -----------------------------------------------------
async function viewLaw(id) {
  loading();
  const law = await api.law(id);
  setHeader(law.name, `${law.version_label || '未註明版本'}　${law.stats?.articles ?? ''} 條`, { back: true });
  setActiveTab('home');

  const renderNodes = (nodes, depth = 0) => nodes.map((node) => {
    if (node.type === 'article') {
      return `
        <a class="tree__article" href="#/article/${node.id}">
          <span class="tree__label">${escapeHtml(node.label)}</span>
          <span class="tree__preview">${node.is_deleted ? '（刪除）' : escapeHtml(node.preview)}</span>
        </a>`;
    }
    return `
      <div class="tree__division" data-level="${node.level}">${escapeHtml(node.heading)}</div>
      <div class="tree__group">${renderNodes(node.children || [], depth + 1)}</div>`;
  }).join('');

  const versions = (law.versions || []).filter((item) => item.id !== law.id);
  render(`
    <div class="card">
      <div class="law-card__meta">
        ${[law.category, law.authority, law.amended && `修正：${law.amended}`, law.promulgated && `公布：${law.promulgated}`]
          .filter(Boolean).map((item) => `<span>${escapeHtml(item)}</span>`).join('')}
      </div>
      <div class="button-row" style="margin-top:12px">
        <a class="button button--small" href="#/search?law=${law.id}">在本法檢索</a>
        ${versions.length ? `<a class="button button--small button--ghost" href="#/diff?law=${encodeURIComponent(law.name)}">版本比對</a>` : ''}
      </div>
    </div>
    <div class="section-title"><span>條文結構</span><span>${law.stats?.divisions ?? 0} 個章節</span></div>
    <div class="tree">${renderNodes(law.tree || [])}</div>
  `);
}

// ---- 條文 ---------------------------------------------------------------
const REFERENCE_PATTERN = /第\s*[0-9一二三四五六七八九十百千]+\s*條(?:\s*之\s*[0-9一二三四五六七八九十]+)?/g;

function linkifyReferences(text, refs, currentLawId) {
  const index = new Map();
  for (const ref of refs || []) {
    if (!ref.to_article_id) continue;
    index.set(`${ref.target_article}-${ref.target_sub || 0}`, ref.to_article_id);
  }
  return escapeHtml(text).replace(REFERENCE_PATTERN, (matched) => {
    const parts = matched.match(/第\s*([0-9一二三四五六七八九十百千]+)\s*條(?:\s*之\s*([0-9一二三四五六七八九十]+))?/);
    const number = chineseToInt(parts[1]);
    const sub = parts[2] ? chineseToInt(parts[2]) : 0;
    const target = index.get(`${number}-${sub}`);
    if (!target) return matched;
    return `<a class="ref-link" href="#/article/${target}">${matched}</a>`;
  });
}

function renderParagraphs(article) {
  const paragraphs = article.paragraphs || article.structure || [];
  if (!paragraphs.length) {
    return `<p class="paragraph">${linkifyReferences(article.text, article.outgoing)}</p>`;
  }
  const renderItems = (items) => items.map((item) => `
    <div class="item item--${item.kind}">
      <strong>${escapeHtml(item.label)}</strong>${linkifyReferences(item.text, article.outgoing)}
      ${item.children?.length ? `<div class="items">${renderItems(item.children)}</div>` : ''}
    </div>`).join('');

  const multiple = paragraphs.length > 1;
  return paragraphs.map((paragraph) => `
    <p class="paragraph">
      ${multiple ? `<span class="paragraph__index">${paragraph.index}</span>` : ''}
      ${linkifyReferences(paragraph.text, article.outgoing)}
    </p>
    ${paragraph.items?.length ? `<div class="items">${renderItems(paragraph.items)}</div>` : ''}
  `).join('');
}

function referenceRow(ref, direction) {
  if (direction === 'in') {
    return `
      <a class="reference" href="#/article/${ref.from_id}">
        <span class="reference__relation" data-relation="${escapeHtml(ref.relation)}">${escapeHtml(ref.relation)}</span>
        <span class="reference__text">${escapeHtml(ref.from_law_name)} ${escapeHtml(ref.from_label)}
          ${ref.context ? `<div class="reference__context">${escapeHtml(ref.context)}</div>` : ''}
        </span>
      </a>`;
  }
  const label = `${ref.target_law || '本法'} 第 ${ref.target_article}${ref.target_sub ? `-${ref.target_sub}` : ''} 條`;
  const inner = `
    <span class="reference__relation" data-relation="${escapeHtml(ref.relation)}">${escapeHtml(ref.relation)}</span>
    <span class="reference__text">${escapeHtml(label)}
      ${ref.raw_text ? `<div class="reference__context">${escapeHtml(ref.raw_text)}</div>` : ''}
    </span>`;
  return ref.to_article_id
    ? `<a class="reference" href="#/article/${ref.to_article_id}">${inner}</a>`
    : `<div class="reference">${inner}</div>`;
}

async function viewArticle(id) {
  loading();
  const article = await api.article(id);
  setHeader(`${article.law_name || ''} ${article.label}`, article.division_path || '', { back: true });
  setActiveTab('home');

  const tags = article.tags || [];
  render(`
    <div class="card">
      <h2 class="article__label">${escapeHtml(article.label_chinese || article.label)}</h2>
      ${article.division_path ? `<p class="article__path">${escapeHtml(article.division_path)}</p>` : ''}
      <div class="article__body">
        ${article.is_deleted ? '<p class="paragraph">（刪除）</p>' : renderParagraphs(article)}
      </div>
      <div class="toolbar">
        <button class="button button--small ${article.bookmarked ? '' : 'button--ghost'}" data-action="bookmark">
          ${article.bookmarked ? '★ 已收藏' : '☆ 收藏'}
        </button>
        <button class="button button--small button--ghost" data-action="add-tag">＋ 標籤</button>
        <button class="button button--small button--ghost" data-action="copy">複製條文</button>
      </div>
    </div>

    <div class="section-title"><span>標籤</span></div>
    <div class="card">
      <div class="tag-row" id="tag-row">
        ${tags.length
          ? tags.map((tag) => `
            <span class="tag-pill">${escapeHtml(tag.name)}
              <button data-remove-tag="${escapeHtml(tag.name)}" aria-label="移除">×</button>
            </span>`).join('')
          : '<span class="hit__snippet">尚無標籤，可用來分類爭點、考點或案件類型。</span>'}
      </div>
    </div>

    <div class="section-title"><span>筆記</span></div>
    <div class="card">
      <textarea id="note-body" placeholder="寫下解釋、實務見解、案例或提醒…">${escapeHtml(article.note?.body || '')}</textarea>
      <button class="button button--small" data-action="save-note" style="margin-top:10px">儲存筆記</button>
    </div>

    ${article.outgoing?.length ? `
      <div class="section-title"><span>本條引用</span><span>${article.outgoing.length} 筆</span></div>
      <div class="card">${article.outgoing.map((ref) => referenceRow(ref, 'out')).join('')}</div>` : ''}

    ${article.incoming?.length ? `
      <div class="section-title"><span>被引用於</span><span>${article.incoming.length} 筆</span></div>
      <div class="card">${article.incoming.map((ref) => referenceRow(ref, 'in')).join('')}</div>` : ''}
  `);

  view.onclick = async (event) => {
    const action = event.target.closest('[data-action]')?.dataset.action;
    const removeTag = event.target.closest('[data-remove-tag]')?.dataset.removeTag;

    if (removeTag) {
      await api.removeTag(article.id, removeTag);
      toast(`已移除標籤「${removeTag}」`);
      return viewArticle(id);
    }
    if (action === 'bookmark') {
      const result = await api.toggleBookmark(article.id);
      toast(result.bookmarked === false ? '已取消收藏' : '已收藏');
      return viewArticle(id);
    }
    if (action === 'add-tag') {
      const name = prompt('標籤名稱（例如：考點、爭點、常用）');
      if (!name?.trim()) return undefined;
      await api.addTag(article.id, name.trim());
      toast(`已加上標籤「${name.trim()}」`);
      return viewArticle(id);
    }
    if (action === 'save-note') {
      await api.setNote(article.id, $('#note-body').value);
      toast('筆記已儲存');
      return undefined;
    }
    if (action === 'copy') {
      const text = `${article.law_name} ${article.label_chinese}\n${article.text}`;
      try {
        await navigator.clipboard.writeText(text);
        toast('已複製條文');
      } catch {
        toast('瀏覽器不允許複製');
      }
    }
    return undefined;
  };
}

// ---- 檢索 ---------------------------------------------------------------
async function viewSearch(params) {
  setHeader('檢索', '關鍵字全文檢索');
  setActiveTab('search');
  const query = params.get('q') || '';
  const lawId = params.get('law') || '';
  const [laws, history] = await Promise.all([api.laws(), api.history()]);

  render(`
    <form class="searchbar" id="search-form">
      <input id="search-input" type="search" value="${escapeHtml(query)}" placeholder="輸入關鍵字，例如 損害賠償" enterkeyhint="search" />
      <button class="button" type="submit">搜尋</button>
    </form>
    <label class="field">
      <span>限定法規</span>
      <select id="search-law">
        <option value="">全部法規</option>
        ${laws.map((law) => `<option value="${law.id}" ${String(law.id) === lawId ? 'selected' : ''}>${escapeHtml(law.name)}</option>`).join('')}
      </select>
    </label>
    ${!query && history.length ? `
      <div class="section-title"><span>最近搜尋</span></div>
      <div class="tag-row">
        ${history.map((row) => `<a class="chip" href="#/search?q=${encodeURIComponent(row.query)}">${escapeHtml(row.query)}</a>`).join('')}
      </div>` : ''}
    <div id="search-results"></div>
  `);

  $('#search-form').addEventListener('submit', (event) => {
    event.preventDefault();
    const keyword = $('#search-input').value.trim();
    const law = $('#search-law').value;
    location.hash = `#/search?q=${encodeURIComponent(keyword)}${law ? `&law=${law}` : ''}`;
  });

  if (!query) return;
  const target = $('#search-results');
  target.innerHTML = '<div class="loading">搜尋中…</div>';
  const result = await api.search(query, { lawId });
  const highlight = (text) => escapeHtml(text)
    .replaceAll('【', '<mark>').replaceAll('】', '</mark>')
    .replaceAll(escapeHtml(query), `<mark>${escapeHtml(query)}</mark>`);

  const fuzzy = result.hits.length > 0 && result.hits[0].fuzzy;
  target.innerHTML = `
    <div class="section-title"><span>結果</span><span>${result.count} 筆</span></div>
    ${fuzzy ? `<div class="card"><p class="hit__snippet">找不到完全相符的「${escapeHtml(query)}」，
      以下是字序寬鬆比對的結果（中間允許夾雜其他文字）。</p></div>` : ''}
    ${result.count ? `<div class="list">${result.hits.map((hit) => `
      <a class="card card--tap" href="#/article/${hit.article_id}">
        <div class="hit__head">
          <span class="hit__law">${escapeHtml(hit.law_name)}
            ${hit.law_version ? `<span class="hit__version">${escapeHtml(hit.law_version)}</span>` : ''}
          </span>
          <span class="hit__label">${escapeHtml(hit.label)}</span>
        </div>
        ${hit.division_path ? `<p class="hit__path">${escapeHtml(hit.division_path)}</p>` : ''}
        <p class="hit__snippet">${highlight(hit.snippet)}</p>
      </a>`).join('')}</div>`
      : '<div class="empty">沒有符合的條文。試試更短的關鍵字。</div>'}
  `;
}

// ---- 整理（標籤／筆記／收藏） ------------------------------------------
async function viewOrganize() {
  setHeader('整理', '標籤、筆記與收藏');
  setActiveTab('organize');
  loading();
  const [tags, notes, bookmarks] = await Promise.all([api.tags(), api.notes(), api.bookmarks()]);

  render(`
    <div class="section-title"><span>標籤</span><span>${tags.length} 個</span></div>
    <div class="card">
      ${tags.length
        ? `<div class="tag-row">${tags.map((tag) => `
            <a class="chip" href="#/tag/${encodeURIComponent(tag.name)}">${escapeHtml(tag.name)}
              <span class="chip--muted"> ${tag.usage ?? ''}</span></a>`).join('')}</div>`
        : '<p class="hit__snippet">還沒有標籤。在條文頁按「＋ 標籤」即可建立。</p>'}
    </div>

    <div class="section-title"><span>收藏</span><span>${bookmarks.length} 條</span></div>
    ${bookmarks.length ? `<div class="list">${bookmarks.map((row) => `
      <a class="card card--tap" href="#/article/${row.id}">
        <div class="hit__head"><span class="hit__law">${escapeHtml(row.law_name)}</span><span class="hit__label">${escapeHtml(row.label)}</span></div>
        <p class="hit__snippet">${escapeHtml(row.text.split('\n')[0].slice(0, 70))}</p>
      </a>`).join('')}</div>` : '<div class="card"><p class="hit__snippet">尚無收藏。</p></div>'}

    <div class="section-title"><span>筆記</span><span>${notes.length} 則</span></div>
    ${notes.length ? `<div class="list">${notes.map((note) => `
      <a class="card card--tap" href="#/article/${note.article_id}">
        <div class="hit__head"><span class="hit__law">${escapeHtml(note.law_name)}</span><span class="hit__label">${escapeHtml(note.label)}</span></div>
        <p class="hit__snippet">${escapeHtml(note.body.slice(0, 90))}</p>
        <p class="hit__path">${escapeHtml(formatTimestamp(note.updated_at))}</p>
      </a>`).join('')}</div>` : '<div class="card"><p class="hit__snippet">尚無筆記。</p></div>'}
  `);
}

async function viewTag(name) {
  setHeader(`標籤：${name}`, '', { back: true });
  setActiveTab('organize');
  loading();
  let rows = [];
  try {
    rows = await request(`/api/tags/${encodeURIComponent(name)}`);
  } catch {
    rows = [];
    for (const law of state.bundle?.laws || []) {
      for (const article of law.articles) {
        if ((article.tags || []).includes(name)) {
          rows.push({ id: article.id, label: article.label, text: article.text, law_name: law.name });
        }
      }
    }
  }
  render(rows.length ? `<div class="list">${rows.map((row) => `
    <a class="card card--tap" href="#/article/${row.id}">
      <div class="hit__head"><span class="hit__law">${escapeHtml(row.law_name)}</span><span class="hit__label">${escapeHtml(row.label)}</span></div>
      <p class="hit__snippet">${escapeHtml(row.text.split('\n')[0].slice(0, 80))}</p>
    </a>`).join('')}</div>` : '<div class="empty">這個標籤還沒有條文。</div>');
}

// ---- 比對 ---------------------------------------------------------------
async function viewDiff(params) {
  setHeader('修法比對', '比較同一部法規的兩個版本');
  setActiveTab('diff');
  loading();
  const laws = await api.laws();
  const grouped = new Map();
  for (const law of laws) {
    grouped.set(law.name, [...(grouped.get(law.name) || []), law]);
  }
  const comparable = [...grouped.entries()].filter(([, items]) => items.length > 1);
  const selectedName = params.get('law') || comparable[0]?.[0] || '';
  const versions = grouped.get(selectedName) || [];

  render(`
    <div class="card">
      ${comparable.length ? `
        <label class="field"><span>法規</span>
          <select id="diff-law">
            ${comparable.map(([name]) => `<option ${name === selectedName ? 'selected' : ''}>${escapeHtml(name)}</option>`).join('')}
          </select>
        </label>
        <label class="field"><span>舊版</span>
          <select id="diff-from">${versions.map((law, index) => `<option value="${law.id}" ${index === 0 ? 'selected' : ''}>${escapeHtml(law.version_label || `#${law.id}`)}</option>`).join('')}</select>
        </label>
        <label class="field"><span>新版</span>
          <select id="diff-to">${versions.map((law, index) => `<option value="${law.id}" ${index === versions.length - 1 ? 'selected' : ''}>${escapeHtml(law.version_label || `#${law.id}`)}</option>`).join('')}</select>
        </label>
        <button class="button button--block" data-action="run-diff">開始比對</button>`
        : `<p>要比對修法前後，需要同一部法規的兩個版本。</p>
           <p class="hit__snippet">匯入時用 <code>--version</code> 標示版本，例如：<br>
           <code>law import 民法-110.txt --version 110.01.13</code><br>
           <code>law import 民法-112.txt --version 112.06.21</code></p>`}
    </div>
    <div id="diff-result"></div>
  `);

  $('#diff-law')?.addEventListener('change', (event) => {
    location.hash = `#/diff?law=${encodeURIComponent(event.target.value)}`;
  });

  $('[data-action="run-diff"]')?.addEventListener('click', async () => {
    const fromId = $('#diff-from').value;
    const toId = $('#diff-to').value;
    if (fromId === toId) return toast('請選擇兩個不同的版本');
    const target = $('#diff-result');
    target.innerHTML = '<div class="loading">比對中…</div>';
    try {
      const result = await api.diff(fromId, toId);
      const changes = result.changes.filter((change) => change.status !== '未變動');
      target.innerHTML = `
        <div class="section-title"><span>結果</span><span>${result.old_version} → ${result.new_version}</span></div>
        <div class="diff-summary">
          ${Object.entries(result.summary).filter(([, count]) => count).map(([status, count]) =>
            `<span class="chip ${status === '刪除' ? 'chip--del' : status === '新增' ? 'chip--accent' : ''}">${status} ${count}</span>`).join('')}
        </div>
        ${changes.length ? changes.map((change) => `
          <div class="card diff-change">
            <div class="diff-change__head">
              <span class="hit__label">${escapeHtml(change.label)}</span>
              <span class="chip ${change.status === '刪除' ? 'chip--del' : 'chip--accent'}">${change.status}</span>
              ${change.status === '修正' ? `<span class="chip chip--muted">相似度 ${Math.round(change.similarity * 100)}%</span>` : ''}
            </div>
            <div class="diff-body">${(change.segments || []).map((segment) => {
              const text = escapeHtml(segment.text);
              if (segment.kind === 'insert') return `<span class="diff-ins">${text}</span>`;
              if (segment.kind === 'delete') return `<span class="diff-del">${text}</span>`;
              return text;
            }).join('')}</div>
          </div>`).join('')
          : '<div class="empty">兩個版本沒有差異。</div>'}
      `;
    } catch (error) {
      target.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    }
    return undefined;
  });
}

// ---- 匯入 ---------------------------------------------------------------
function viewImport() {
  setHeader('匯入法規', '貼上條文，自動解析結構', { back: true });
  setActiveTab('home');
  render(`
    <div class="card">
      <label class="field"><span>法規名稱（可留空，自動判斷）</span>
        <input type="text" id="import-name" placeholder="例如：中華民國民法" /></label>
      <label class="field"><span>版本標籤（可留空）</span>
        <input type="text" id="import-version" placeholder="例如：110.01.13" /></label>
      <label class="field"><span>條文內容</span>
        <textarea id="import-text" style="min-height:220px" placeholder="第 1 條&#10;民事，法律所未規定者，依習慣；無習慣者，依法理。&#10;第 2 條&#10;……"></textarea></label>
      <button class="button button--block" data-action="do-import">解析並匯入</button>
      <p class="hit__snippet" style="margin-top:12px">
        支援全國法規資料庫的純文字格式，會自動辨識編章節、條、項、款、目與交互參照。
        大量檔案建議在電腦上執行 <code>law import 資料夾</code>。
      </p>
    </div>
  `);

  $('[data-action="do-import"]').addEventListener('click', async (event) => {
    const text = $('#import-text').value;
    if (!text.trim()) return toast('請先貼上條文');
    event.target.disabled = true;
    event.target.textContent = '匯入中…';
    try {
      const result = await api.importText({
        text,
        name: $('#import-name').value.trim(),
        version: $('#import-version').value.trim(),
        replace: true,
      });
      toast(`已匯入 ${result.name}，共 ${result.stats.articles} 條`);
      await refreshBundle();
      location.hash = `#/law/${result.law_id}`;
    } catch (error) {
      toast(error instanceof OfflineError ? '匯入需要連上伺服器' : error.message);
      event.target.disabled = false;
      event.target.textContent = '解析並匯入';
    }
    return undefined;
  });
}

// ---- 設定 ---------------------------------------------------------------
async function viewSettings() {
  setHeader('設定', 'App 與資料位置');
  setActiveTab('settings');
  loading();
  const meta = state.meta || (await api.meta());
  const paths = meta?.paths || {};

  render(`
    <div class="section-title"><span>知識庫位置</span></div>
    <div class="card">
      <table class="path-table">
        ${Object.entries(paths).map(([key, value]) =>
          `<tr><th>${escapeHtml(key)}</th><td>${escapeHtml(String(value))}</td></tr>`).join('')}
      </table>
    </div>

    <div class="section-title"><span>離線資料</span></div>
    <div class="card">
      <p class="hit__snippet">
        ${state.bundleAt ? `目前離線資料產生於 ${escapeHtml(formatTimestamp(state.bundleAt))}` : '尚未下載離線資料。'}
      </p>
      <div class="button-row" style="margin-top:10px">
        <button class="button button--small" data-action="sync-bundle">更新離線資料</button>
        <button class="button button--small button--ghost" data-action="flush">補送離線變更</button>
      </div>
    </div>

    <div class="section-title"><span>閱讀</span></div>
    <div class="card">
      <label class="field"><span>字體大小：<b id="size-value">${settings.data.readingSize}</b> px</span>
        <input type="range" id="size-range" min="14" max="24" value="${settings.data.readingSize}" style="width:100%" /></label>
      <label class="field"><span>外觀</span>
        <select id="theme-select">
          ${['auto', 'light', 'dark'].map((value) => `<option value="${value}" ${settings.data.theme === value ? 'selected' : ''}>
            ${{ auto: '跟隨系統', light: '淺色', dark: '深色' }[value]}</option>`).join('')}
        </select></label>
    </div>

    <div class="section-title"><span>匯出</span></div>
    <div class="card">
      <div class="button-row">
        ${(meta?.exporters || []).map((item) => `
          <button class="button button--small button--ghost" data-export="${item.name}" title="${escapeHtml(item.description)}">
            ${escapeHtml(item.name)}
          </button>`).join('') || '<span class="hit__snippet">需連上伺服器才能匯出。</span>'}
      </div>
      <p class="hit__snippet" style="margin-top:10px">匯出檔案會下載到手機／電腦，也可在電腦上執行 <code>law export markdown</code>。</p>
    </div>

    <div class="section-title"><span>關於</span></div>
    <div class="card">
      <p class="hit__snippet">法規整理 App v${APP_VERSION}　資料庫結構 v${meta?.stats?.schema_version ?? '-'}</p>
      <p class="hit__snippet">在手機瀏覽器選擇「加入主畫面」即可像 App 一樣使用，離線也能查閱。</p>
    </div>
  `);

  $('#size-range').addEventListener('input', (event) => {
    $('#size-value').textContent = event.target.value;
    settings.save({ readingSize: Number(event.target.value) });
  });
  $('#theme-select').addEventListener('change', (event) => settings.save({ theme: event.target.value }));
  $('[data-action="sync-bundle"]').addEventListener('click', () => refreshBundle({ silent: false }));
  $('[data-action="flush"]').addEventListener('click', flushPending);

  for (const button of document.querySelectorAll('[data-export]')) {
    button.addEventListener('click', async () => {
      try {
        const result = await api.exportAs(button.dataset.export);
        const blob = new Blob([result.content], { type: 'text/plain;charset=utf-8' });
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = `lawkit-${button.dataset.export}.txt`;
        link.click();
        URL.revokeObjectURL(link.href);
      } catch (error) {
        toast(error.message);
      }
    });
  }
}

// ------------------------------------------------------------------ 路由
const routes = [
  [/^\/?$/, () => viewHome()],
  [/^\/law\/(\d+)$/, (match) => viewLaw(match[1])],
  [/^\/article\/(\d+)$/, (match) => viewArticle(match[1])],
  [/^\/search$/, (match, params) => viewSearch(params)],
  [/^\/organize$/, () => viewOrganize()],
  [/^\/tag\/(.+)$/, (match) => viewTag(decodeURIComponent(match[1]))],
  [/^\/diff$/, (match, params) => viewDiff(params)],
  [/^\/import$/, () => viewImport()],
  [/^\/settings$/, () => viewSettings()],
];

async function routeTo() {
  const raw = location.hash.replace(/^#/, '') || '/';
  const [path, queryString = ''] = raw.split('?');
  const params = new URLSearchParams(queryString);
  for (const [pattern, handler] of routes) {
    const match = path.match(pattern);
    if (!match) continue;
    try {
      await handler(match, params);
    } catch (error) {
      render(`<div class="empty">${escapeHtml(error.message || '發生錯誤')}</div>`);
    }
    return;
  }
  render('<div class="empty">找不到這個畫面。</div>');
}

// ------------------------------------------------------------------ 啟動
async function boot() {
  settings.load();
  applySettings();

  $('#back-button').addEventListener('click', () => history.back());
  $('#theme-button').addEventListener('click', () => {
    const order = ['auto', 'light', 'dark'];
    const next = order[(order.indexOf(settings.data.theme) + 1) % order.length];
    settings.save({ theme: next });
    toast({ auto: '跟隨系統', light: '淺色模式', dark: '深色模式' }[next]);
  });
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applySettings);
  window.addEventListener('hashchange', routeTo);
  window.addEventListener('online', () => { setOnline(true); routeTo(); });
  window.addEventListener('offline', () => setOnline(false));

  await loadBundleFromCache().catch(() => null);
  await api.meta().catch(() => null);
  await routeTo();

  if (settings.data.autoBundle && state.online) {
    refreshBundle().catch(() => {});
    flushPending().catch(() => {});
  }

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('sw.js').catch(() => {});
  }
}

boot();
