const state = {
  selectedCompany: null,
  reports: [],
  extracted: null,
  fields: [],
  saveBasePayload: null
};

const $ = id => document.getElementById(id);
const statusBox = $('statusBox');
const dropdown = $('companyDropdown');

function setStatus(message, obj, mode = 'idle') {
  if (statusBox) {
    statusBox.textContent = obj ? `${message}\n${JSON.stringify(obj, null, 2)}` : message;
  }
  const pill = $('statusPill');
  if (pill) {
    pill.textContent = mode === 'ok' ? 'OK' : mode === 'error' ? 'Attention' : mode === 'working' ? 'Working' : 'Idle';
    pill.className = `status-pill ${mode}`;
  }
  if (mode === 'error') console.error(message, obj || '');
  else console.log(message, obj || '');
}

async function api(url, options) {
  const response = await fetch(url, options);
  const text = await response.text();
  let data;
  try { data = JSON.parse(text); } catch { data = { ok: response.ok, raw: text }; }
  if (!response.ok || data.ok === false) throw data;
  return data;
}

async function loadFields() {
  state.fields = await api('/api/fields');
}

function initialiseYears() {
  const select = $('yearSelect');
  const current = new Date().getFullYear();
  const start = 2022;
  const end = Math.max(current + 1, 2026);
  select.innerHTML = '';
  for (let y = end; y >= start; y--) {
    const opt = document.createElement('option');
    opt.value = String(y);
    opt.textContent = String(y);
    if (y === current - 1 || y === 2025) opt.selected = true;
    select.appendChild(opt);
  }
}

let companyTimer = null;
$('companyInput').addEventListener('input', () => {
  clearTimeout(companyTimer);
  clearCompanySelectionIfManualEdit();
  companyTimer = setTimeout(searchCompanies, 120);
});
$('companyInput').addEventListener('focus', () => {
  if ($('companyInput').value.trim()) searchCompanies();
});

async function searchCompanies() {
  const term = $('companyInput').value.trim();
  if (term.length < 1) { dropdown.classList.add('hidden'); return; }

  let companies = [];
  try { companies = await api('/api/companies?term=' + encodeURIComponent(term)); }
  catch (err) { setStatus('Company search failed', err, 'error'); return; }

  dropdown.innerHTML = '';
  const typedSymbol = getTypedSymbolCandidate();
  if (typedSymbol && !companies.some(c => (c.symbol || '').toUpperCase() === typedSymbol)) {
    const div = document.createElement('div');
    div.className = 'dropdown-item manual-symbol';
    div.innerHTML = `<strong>${escapeHtml(typedSymbol)}</strong><span>Use typed PSX symbol directly</span>`;
    div.onclick = () => selectTypedSymbol(typedSymbol);
    dropdown.appendChild(div);
  }

  companies.forEach(c => {
    const div = document.createElement('div');
    div.className = 'dropdown-item';
    div.innerHTML = `<strong>${escapeHtml(c.symbol || '')}</strong><span>${escapeHtml(c.name || '')}</span>`;
    div.onclick = () => selectCompany(c);
    dropdown.appendChild(div);
  });

  if (!dropdown.children.length) {
    const empty = document.createElement('div');
    empty.className = 'dropdown-item muted';
    empty.textContent = 'No matching company found. Type an exact PSX symbol to continue.';
    dropdown.appendChild(empty);
  }
  dropdown.classList.remove('hidden');
}

function getTypedSymbolCandidate() {
  const typed = ($('companyInput').value || '').trim();
  if (!typed) return '';
  const firstToken = typed.split(/\s+/)[0].trim().toUpperCase();
  return /^[A-Z0-9]{2,12}$/.test(firstToken) ? firstToken : '';
}

function clearCompanySelectionIfManualEdit() {
  const typed = ($('companyInput').value || '').trim();
  const expected = state.selectedCompany
    ? `${state.selectedCompany.symbol || ''} ${state.selectedCompany.name || ''}`.trim()
    : '';
  if (!state.selectedCompany || typed !== expected) {
    state.selectedCompany = null;
    $('symbolInput').value = '';
    $('companyNameInput').value = '';
    updateSelectedCompanyCard();
  }
}

function selectTypedSymbol(symbol) {
  state.selectedCompany = { symbol, name: '', compCode: '' };
  $('companyInput').value = symbol;
  $('symbolInput').value = symbol;
  $('companyNameInput').value = '';
  dropdown.classList.add('hidden');
  updateSelectedCompanyCard();
}

function selectCompany(c) {
  state.selectedCompany = c;
  $('companyInput').value = `${c.symbol || ''} ${c.name || ''}`.trim();
  $('symbolInput').value = c.symbol || '';
  $('companyNameInput').value = c.name || '';
  dropdown.classList.add('hidden');
  updateSelectedCompanyCard();
}

function updateSelectedCompanyCard() {
  const symbol = ($('symbolInput').value || getTypedSymbolCandidate() || '').toUpperCase();
  const company = $('companyNameInput').value || (state.selectedCompany && state.selectedCompany.name) || '';
  $('selectedCompanyText').textContent = company || (symbol ? 'Typed symbol only' : 'No company selected');
  $('selectedSymbolText').textContent = symbol || '-';
}

document.addEventListener('click', e => {
  if (!dropdown.contains(e.target) && e.target !== $('companyInput')) dropdown.classList.add('hidden');
});

$('findReportsBtn').onclick = async () => {
  const payload = getBasePayload();
  if (!payload.symbol) {
    showNoReports('Please select a company or enter a PSX symbol.', '');
    setStatus('Please select a company or enter a PSX symbol.', null, 'error');
    return;
  }

  clearExtractionUi();
  hideNoReports();
  setStatus('Finding reports from financials.psx.com.pk only. Please wait...', payload, 'working');
  $('findReportsBtn').disabled = true;
  $('extractBtn').disabled = true;
  try {
    const data = await api('/api/reports', postJson(payload));
    state.reports = data.reports || [];

    if (data.noReportsFound || state.reports.length === 0) {
      const symbol = data.symbol || payload.symbol || 'selected company';
      const year = data.year || payload.year || '';
      const message = data.message || `No reports found for ${symbol} in ${year}.`;
      renderReports([], message);
      showNoReports(message, 'Try another year, confirm the PSX symbol, or verify the report exists on financials.psx.com.pk.');
      setStatus(message, data, 'error');
      return;
    }

    hideNoReports();
    renderReports(state.reports);
    setStatus(`Found ${state.reports.length} report(s).`, data, 'ok');
    updateExtractButtonState();
  } catch (err) {
    renderReports([], 'Report fetch failed.');
    showNoReports('Report fetch failed.', 'Check internet/PSX availability and try again.');
    setStatus('Report fetch failed', err, 'error');
  } finally {
    $('findReportsBtn').disabled = false;
  }
};

function getBasePayload() {
  const typedSymbol = getTypedSymbolCandidate();
  const selectedSymbol = ($('symbolInput').value || '').trim().toUpperCase();
  const symbol = selectedSymbol || typedSymbol;
  const selectedMatchesTyped = state.selectedCompany &&
    (state.selectedCompany.symbol || '').toUpperCase() === symbol &&
    ($('companyInput').value || '').trim() === `${state.selectedCompany.symbol || ''} ${state.selectedCompany.name || ''}`.trim();

  return {
    symbol,
    companyName: selectedMatchesTyped ? $('companyNameInput').value.trim() : '',
    compCode: '',
    year: parseInt($('yearSelect').value, 10),
    reportType: 'All'
  };
}

function renderReports(reports, emptyMessage) {
  const tbody = $('reportsTable').querySelector('tbody');
  tbody.innerHTML = '';
  if (!reports.length) {
    const message = emptyMessage || 'No reports loaded yet.';
    tbody.innerHTML = `<tr><td colspan="6" class="empty-report-message">${escapeHtml(message)}</td></tr>`;
    updateExtractButtonState();
    return;
  }
  reports.forEach((r, idx) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><input type="radio" name="selectedReport" class="reportCheck" data-index="${idx}" ${idx === 0 ? 'checked' : ''} /></td>
      <td><span class="badge report-badge">${escapeHtml(formatReportType(r.reportType || 'Report'))}</span></td>
      <td>${escapeHtml(r.periodEnded || '-')}</td>
      <td>${escapeHtml(r.published || '-')}</td>
      <td>${escapeHtml(r.title || '')}</td>
      <td class="urlcell"><a href="${escapeAttr(r.url || '#')}" target="_blank" rel="noreferrer">Open PDF</a></td>`;
    tbody.appendChild(tr);
  });
  document.querySelectorAll('.reportCheck').forEach(input => {
    input.addEventListener('change', updateExtractButtonState);
  });
  updateExtractButtonState();
}

function updateExtractButtonState() {
  const extractBtn = $('extractBtn');
  const selected = document.querySelector('.reportCheck:checked');
  extractBtn.disabled = !selected;
}

$('extractBtn').onclick = async () => {
  const selectedInput = document.querySelector('.reportCheck:checked');
  if (!selectedInput) { alert('Select one report to extract.'); return; }
  const selectedReport = state.reports[parseInt(selectedInput.dataset.index, 10)];
  if (!selectedReport) { alert('Selected report could not be found. Please reload the reports.'); return; }
  const base = getBasePayload();
  const payload = { ...base, reports: [selectedReport] };
  setStatus('Downloading selected PDF and extracting values...', payload, 'working');
  $('extractBtn').disabled = true;
  try {
    const data = await api('/api/extract', postJson(payload));
    state.extracted = data;
    renderResultHeader(data.reports || [], base);
    renderSummaryCards(data.reports || []);
    renderMatrix(data.reports || []);
    renderWarnings(data);
    state.saveBasePayload = base;
    showSaveControls();
    setStatus('Extraction completed.', data.summary || data, 'ok');
  } catch (err) {
    alert('Extraction failed. Please try again or open the PDF to confirm it is available.');
    setStatus('Extraction failed', err, 'error');
  } finally {
    updateExtractButtonState();
  }
};


function showSaveControls() {
  const btn = $('saveDbBtn');
  const status = $('saveDbStatus');
  if (btn) {
    btn.classList.remove('hidden');
    btn.disabled = false;
  }
  if (status) {
    status.classList.add('hidden');
    status.textContent = '';
    status.className = 'save-status hidden';
  }
}

function hideSaveControls() {
  const btn = $('saveDbBtn');
  const status = $('saveDbStatus');
  if (btn) {
    btn.classList.add('hidden');
    btn.disabled = true;
  }
  if (status) {
    status.classList.add('hidden');
    status.textContent = '';
    status.className = 'save-status hidden';
  }
}

function setSaveStatus(message, mode = 'idle') {
  const status = $('saveDbStatus');
  if (!status) return;
  status.textContent = message;
  status.className = `save-status ${mode}`;
  status.classList.remove('hidden');
}

function getReportsWithEditedValues() {
  const reports = (state.extracted?.reports || []).map(report => ({
    ...report,
    values: { ...(report.values || {}) }
  }));

  document.querySelectorAll('.matrixValue').forEach(input => {
    const reportIndex = parseInt(input.dataset.report || '-1', 10);
    const field = input.dataset.field || '';
    if (!Number.isNaN(reportIndex) && reports[reportIndex] && field) {
      const value = input.value.trim();
      reports[reportIndex].values[field] = value === '' ? null : value;
    }
  });

  return reports;
}

$('saveDbBtn').onclick = async () => {
  if (!state.extracted || !(state.extracted.reports || []).length) {
    alert('Please extract a report first.');
    return;
  }

  const base = state.saveBasePayload || getBasePayload();
  const payload = {
    ...base,
    companyName: base.companyName || state.selectedCompany?.name || '',
    reports: getReportsWithEditedValues()
  };

  $('saveDbBtn').disabled = true;
  setSaveStatus('Saving to database...', 'working');

  try {
    const data = await api('/api/save', postJson(payload));
    if (data.dbInserted) {
      setSaveStatus(`Saved ${data.dbRowsInserted || data.rows || 0} row(s) to database.`, 'ok');
    } else if (data.dbInsertEnabled === false) {
      setSaveStatus('SQL file generated, but database saving is not enabled in appsettings.json.', 'warning');
    } else {
      setSaveStatus(data.note || 'Save completed.', 'ok');
    }
    setStatus('Save completed.', data, 'ok');
  } catch (err) {
    const message = err.dbError || err.error || 'Database save failed. Check SQL connection, pyodbc and dbo.BalnShet.';
    setSaveStatus(message, 'error');
    setStatus('Database save failed', err, 'error');
  } finally {
    $('saveDbBtn').disabled = false;
  }
};

function renderResultHeader(reports, base) {
  const company = state.selectedCompany?.name || base.companyName || base.symbol || 'Selected company';
  const types = [...new Set(reports.map(r => formatReportType(r.reportType || 'Report')))];
  $('resultHeading').textContent = `${company} — ${base.year}`;
  $('resultSubheading').textContent = reports.length
    ? `${types.join(', ')}${reports[0]?.tranDate || reports[0]?.periodEnded ? ' — ' + (reports[0].tranDate || reports[0].periodEnded) : ''}`
    : 'No extracted data yet.';
}

function renderSummaryCards(reports) {
  const box = $('summaryCards');
  if (!reports.length) { box.classList.add('hidden'); box.innerHTML = ''; return; }
  const totalValues = reports.reduce((sum, r) => sum + Object.values(r.values || {}).filter(v => v !== null && v !== '').length, 0);
  const warningCount = reports.reduce((sum, r) => sum + (r.warnings || []).length, 0);
  const directCount = reports.reduce((sum, r) => sum + Object.keys(r.evidence || {}).filter(f => classifyValue(r, f, r.values?.[f]).type === 'verified').length, 0);
  box.innerHTML = `
    <div><span>Reports</span><strong>${reports.length}</strong></div>
    <div><span>Extracted values</span><strong>${totalValues}</strong></div>
    <div><span>Direct evidence</span><strong>${directCount}</strong></div>
    <div><span>Warnings</span><strong>${warningCount}</strong></div>`;
  box.classList.remove('hidden');
}

function renderMatrix(reports) {
  const table = $('matrixTable');
  const thead = table.querySelector('thead');
  const tbody = table.querySelector('tbody');
  const headerCells = reports.map((r, i) => `
    <th>
      <div class="report-column-title">${escapeHtml(formatReportType(r.reportType || 'Report ' + (i + 1)))}</div>
      <small>${escapeHtml(r.tranDate || r.periodEnded || '')}</small>
    </th>`).join('');
  thead.innerHTML = `<tr><th class="field-head">Financial Statement Field</th>${headerCells}</tr>`;
  tbody.innerHTML = '';
  if (!reports.length) {
    tbody.innerHTML = '<tr><td class="muted">No extracted data.</td></tr>';
    return;
  }
  state.fields.forEach(field => {
    const tr = document.createElement('tr');
    let html = `<td class="field-name"><strong>${escapeHtml(formatFieldName(field))}</strong><small>${escapeHtml(field)}</small></td>`;
    reports.forEach((r, reportIndex) => {
      const hasValue = r.values && Object.prototype.hasOwnProperty.call(r.values, field) && r.values[field] !== null && r.values[field] !== '';
      const val = hasValue ? r.values[field] : '';
      const cls = classifyValue(r, field, val);
      const evidence = (r.evidence && r.evidence[field]) || '';
      html += `
        <td class="value-cell ${cls.type}">
          <div class="value-input-wrap" title="${escapeAttr(cls.label + (evidence ? ' — ' + evidence : ''))}">
            ${confidenceIcon(cls.type)}
            <input class="matrixValue" data-report="${reportIndex}" data-field="${escapeAttr(field)}" value="${escapeAttr(String(val))}" />
          </div>
          <div class="value-status ${cls.type}">${escapeHtml(cls.label)}</div>
        </td>`;
    });
    tr.innerHTML = html;
    tbody.appendChild(tr);
  });
}

function formatFieldName(field) {
  return String(field || '').replace(/([a-z0-9])([A-Z])/g, '$1 $2');
}

function classifyValue(report, field, value) {
  if (value === null || value === undefined || value === '') return { type: 'missing', label: 'Missing' };
  const evidence = String((report.evidence && report.evidence[field]) || '').toLowerCase();
  const warnings = (report.warnings || []).join(' ').toLowerCase();
  const fieldMentionedInReviewWarning = warnings.includes(field.toLowerCase()) && (warnings.includes('manual review') || warnings.includes('not found'));

  if (evidence.includes('calculated') || evidence.includes('ratio') || evidence.includes('inferred')) {
    return fieldMentionedInReviewWarning ? { type: 'review', label: 'Calculated review' } : { type: 'calculated', label: 'Calculated' };
  }
  if (evidence.includes('pdf page') || evidence.includes('ocr pdf page') || evidence.includes('statement row')) {
    return fieldMentionedInReviewWarning ? { type: 'review', label: 'Direct review' } : { type: 'verified', label: 'Verified' };
  }
  return { type: 'review', label: 'Needs review' };
}

function confidenceIcon(type) {
  if (type === 'verified') return '<span class="confidence-dot verified"></span>';
  if (type === 'calculated') return '<span class="confidence-dot calculated"></span>';
  if (type === 'review') return '<span class="confidence-triangle review"></span>';
  return '<span class="confidence-dot missing"></span>';
}

function renderWarnings(data) {
  const box = $('warningsBox');
  const warnings = [];
  (data.reports || []).forEach(r => (r.warnings || []).forEach(w => warnings.push(`${formatReportType(r.reportType || r.title || 'Report')}: ${w}`)));
  box.innerHTML = warnings.map(w => `<div>• ${escapeHtml(w)}</div>`).join('');
  box.classList.toggle('hidden', warnings.length === 0);
}

function showNoReports(title, detail) {
  const box = $('noReportsBox');
  box.innerHTML = `<strong>${escapeHtml(title)}</strong><span>${escapeHtml(detail || '')}</span>`;
  box.classList.remove('hidden');
}
function hideNoReports() { $('noReportsBox').classList.add('hidden'); $('noReportsBox').innerHTML = ''; }
function clearExtractionUi() {
  state.extracted = null;
  state.saveBasePayload = null;
  $('summaryCards').classList.add('hidden');
  $('warningsBox').classList.add('hidden');
  $('resultHeading').textContent = 'Extracted values table';
  $('resultSubheading').textContent = 'After extraction, company, year and report details will appear here.';
  hideSaveControls();
}
function formatReportType(value) {
  const v = String(value || '').trim();
  if (v === 'Q1') return 'First Quarter / Q1';
  if (v === 'Q3 / Nine Months' || v === 'Q3') return 'Third Quarter / Nine Months';
  if (v === 'Half Year') return 'Half-Year / Interim';
  if (v === 'Annual') return 'Annual Financial Statements';
  return v || 'Report';
}
function postJson(payload) {
  return { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) };
}
function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[ch]));
}
function escapeAttr(value) { return escapeHtml(value); }

initialiseYears();
updateSelectedCompanyCard();
loadFields().catch(err => setStatus('Could not load BalnShet fields', err, 'error'));
