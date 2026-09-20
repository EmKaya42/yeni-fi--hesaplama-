const $ = selector => document.querySelector(selector);
const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const money = value => value === '' || value == null ? '—' : new Intl.NumberFormat('tr-TR',{style:'currency',currency:'TRY'}).format(Number(value));
const date = value => value ? new Intl.DateTimeFormat('tr-TR',{dateStyle:'medium'}).format(new Date(value)) : '—';
const statusLabels = {success:['✓','Kontrolleri geçti'],review:['!','İnceleme gerekli'],failed:['!','Okunamadı'],queued:['◷','Sırada'],processing:['◌','Okunuyor'],duplicate:['↳','Mükerrer'],mapping:['!','Hesap eşleşmesi gerekli']};
const badge = status => `<span class="badge badge-${escape(status)}">${(statusLabels[status] || ['','Bilinmiyor']).map(escape).join(' ')}</span>`;
let kind = 'receipts', offset = 0, settings, draft, authUser, auth, authApi, pollTimer, toastTimer;
let refreshing = false, uploading = false, uploadItems = [], uploadSequence = 0, refreshVersion = 0, previewUrl, detailVersion = 0;
let registerMode = false, signedIn = false, chartImportMode = 'new';
const scopeValues = () => ({program:settings?.selected?.program || '', chart_id:settings?.selected?.chart_id || '', period:$('#period-filter').value});
const localAuth = document.body.dataset.localAuth === 'true';
function toast(message) { clearTimeout(toastTimer); $('#toast').textContent = message; $('#toast').classList.add('show'); toastTimer = setTimeout(() => $('#toast').classList.remove('show'), 6500); }

async function api(url, options = {}) {
  const headers = new Headers(options.headers);
  if (!localAuth) {
    if (!authUser) throw new Error('Devam etmek için giriş yapın.');
    headers.set('Authorization', `Bearer ${await authUser.getIdToken()}`);
  }
  const response = await fetch(url, {...options, headers});
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || `İşlem tamamlanamadı (${response.status}).`);
  }
  return response;
}
const jsonApi = async (url, options) => (await api(url, options)).json();
function currentProgramName() { return settings?.programs.find(p => p.id === settings.selected?.program)?.name || 'Program seçin'; }
function usesCustomTemplate() { return Boolean(settings?.selected?.template_name && settings.selected.template_kind === kind); }
function renderSelected() {
  $('#selected-program').textContent = `${currentProgramName()} ↗`;
  $('#selected-template').textContent = usesCustomTemplate() ? settings.selected.template_name : kind === 'receipts' ? 'Fiş örneği düzeni · ürün açıklaması' : 'Z raporu örneği düzeni';
  $('#setup-banner').hidden = Boolean(settings.selected);
  $('#choose-files').disabled = !settings.selected;
  $('#workspace-chart').innerHTML = '<option value="">Standart hesaplar / önceki belgeler</option>' + (settings.charts || []).filter(chart => chart.program === settings.selected?.program).map(chart => `<option value="${escape(chart.id)}">${escape(chart.name)}</option>`).join('');
  $('#workspace-chart').value = settings.selected?.chart_id || '';
  $('#bank-count').textContent = `(${settings.banks.length})`;
  $('#bank-catalog').innerHTML = settings.banks.map(bank => `<div><dt>${escape(bank.code)}</dt><dd>${escape(bank.name)}</dd></div>`).join('');
  $('#bank-source').href = settings.bank_source;
}
async function begin() {
  signedIn = true;
  const loginScreen = $('#login-screen') || $('#landing-screen');
  if (loginScreen) {
    loginScreen.hidden = true;
    loginScreen.style.display = 'none';
  }
  const loginModal = $('#login-modal');
  if (loginModal) {
    loginModal.hidden = true;
    loginModal.style.display = 'none';
  }
  const workspace = $('#workspace');
  if (workspace) {
    workspace.hidden = false;
    workspace.style.display = '';
  }
  if ($('#today')) {
    $('#today').textContent = new Intl.DateTimeFormat('tr-TR', {dateStyle:'long'}).format(new Date());
  }
  if ($('#user-name')) {
    $('#user-name').textContent = authUser?.displayName || (localAuth ? 'Yerel çalışma alanı' : 'Muhasebe hesabı');
  }
  if ($('#user-initial')) {
    $('#user-initial').textContent = (authUser?.displayName || 'M').charAt(0).toUpperCase();
  }
  try {
    settings = await jsonApi('/api/settings');
    if ($('#program-select')) {
      $('#program-select').innerHTML = '<option value="">Program seçin</option>' + settings.programs.map(p => `<option value="${escape(p.id)}">${escape(p.name)}</option>`).join('');
    }
    renderSelected();
    if (!settings.selected) await openSettings();
    await refresh();
    await loadLegacy();
  } catch (error) { toast(error.message); }
  clearTimeout(pollTimer);
  schedulePoll();
}
function schedulePoll() {
  pollTimer = setTimeout(async () => { if (!signedIn) return; await refresh(); schedulePoll(); }, 2500);
}
let renderedDocumentRows = null;
function setDocumentRows(html) {
  if (html === renderedDocumentRows) return;
  $('#document-table').innerHTML = html;
  renderedDocumentRows = html;
}
async function refresh() {
  if (refreshing || !signedIn) return;
  refreshing = true;
  const version = refreshVersion;
  try {
    const query = new URLSearchParams({...scopeValues(), kind, offset, limit:50, status:$('#status-filter').value, search:$('#search').value});
    const data = await jsonApi(`/api/documents?${query}`);
    if (version !== refreshVersion) return;
    const c = data.counts;
    const pending = data.pending ?? ((c.queued || 0) + (c.processing || 0));
    const review = (c.review || 0) + (c.failed || 0) + (c.mapping || 0);
    const selectedPeriod = $('#period-filter').value;
    $('#period-filter').innerHTML = '<option value="">Tüm dönemler</option>' + [...new Set([...(data.periods || []), ...(selectedPeriod ? [selectedPeriod] : [])])].sort().reverse().map(period => `<option value="${escape(period)}">${escape(period)}</option>`).join('');
    $('#period-filter').value = selectedPeriod;
    $('#export-history-caption').textContent = data.exported ? `${data.exported} belge için daha önce Excel hazırlandı.` : 'Bu belgeler için henüz Excel hazırlanmadı.';
    $('#count-total').textContent = Object.values(c).reduce((sum, n) => sum + n, 0);
    $('#count-success').textContent = c.success || 0;
    $('#count-review').textContent = review;
    $('#count-pending').textContent = pending;
    $('#result-count').textContent = data.total;
    const queue = data.queue_counts || c;
    $('#queue-caption').textContent = pending ? `${queue.processing || 0} belge okunuyor · ${queue.queued || 0} belge sırada` : review ? `${review} belge için inceleme veya yeniden deneme gerekiyor.` : 'Okuma kuyruğu tamamlandı.';
    $('#export-button').disabled = !settings?.selected || !(c.success > 0) || pending > 0 || (c.mapping || 0) > 0 || uploading;
    $('#export-button').title = pending ? 'Okuma kuyruğu tamamlandıktan sonra indirebilirsiniz.' : c.mapping ? 'Hesap eşleşmesi gereken belgeleri inceleyin.' : `${c.success || 0} belge Excel’e alınacak.`;
    $('#empty-state').hidden = data.items.length > 0;
    $('#empty-state h3').textContent = data.total === 0 && ($('#search').value || $('#status-filter').value) ? 'Aramanızla eşleşen belge yok' : 'İlk belgenizi ekleyin';
    const tableHtml = data.items.map(row => {
      const result = row.result;
      const warning = row.error || result.issues?.[0] || '';
      return `<tr><td><div class="file-name"><span class="file-icon">▤</span><div><b title="${escape(row.filename)}">${escape(row.filename)}</b><small>${escape(result.document_no || 'Belge no bekleniyor')}${row.filename.toLowerCase().endsWith('.pdf') ? ` · Sayfa ${row.page + 1}` : ''} · ${row.direction === 'income' ? 'Gelir' : 'Gider'}</small></div></div></td><td><b>${escape(result.seller_name || '—')}</b>${row.kind === 'receipts' ? `<small class="product-summary" title="${escape(result.product_name || '')}">${escape(result.product_name || 'Ürün adı bekleniyor')}</small>` : ''}<small>${escape(date(result.document_datetime))}</small></td><td>${escape(money(result.vat_amount))}<small>${escape(result.vat_breakdown?.map(v => `%${v.rate}`).join(' / ') || '')}</small></td><td><b>${escape(money(result.total_amount))}</b></td><td title="${escape(warning)}">${badge(row.status)}${warning ? `<small class="document-warning">${escape(warning)}</small>` : ''}<small>${row.attempts ? `${row.attempts}. deneme` : 'Okuma bekliyor'}${row.auto_retries ? ' · otomatik yeniden denendi' : ''}</small>${row.export_count ? `<small>Excel hazırlandı (${row.export_count})</small>` : ''}</td><td><div class="row-actions"><button data-detail="${row.id}">İncele</button>${['review','failed','mapping'].includes(row.status) ? `<button class="retry" data-retry="${row.id}">↻ Yeniden dene</button>` : ''}</div></td></tr>`;
    }).join('');
    setDocumentRows(tableHtml);
    $('#pagination-label').textContent = data.total ? `${offset + 1}–${Math.min(offset + 50, data.total)} / ${data.total} belge` : '0 belge';
    $('#previous-page').disabled = offset === 0;
    $('#next-page').disabled = offset + 50 >= data.total;
    if (offset >= data.total && offset > 0) { offset = Math.max(0, Math.ceil(data.total / 50 - 1) * 50); }
  } catch (error) { $('#queue-caption').textContent = error.message; }
  finally { refreshing = false; }
}
async function loadLegacy() {
  const view = kind;
  if (settings?.selected?.chart_id) { $('#legacy-section').hidden = true; return; }
  try {
    const data = await jsonApi(`/api/legacy?kind=${kind}`);
    if (view !== kind) return;
    $('#legacy-section').hidden = data.items.length === 0;
    $('#legacy-count').textContent = `(${data.items.length})`;
    $('#legacy-table').innerHTML = data.items.map(row => `<tr><td>${escape(row.receipt_no || row.report_no || row.invoice_no || '—')}</td><td>${escape(row.product_name || '—')}</td><td>${escape(row.receipt_datetime || row.report_datetime || '—')}</td><td>${escape(money(row.total_amount ?? row.daily_turnover))}</td></tr>`).join('');
  } catch (error) { toast(error.message); }
}
document.querySelectorAll('[data-kind]').forEach(button => button.addEventListener('click', async () => {
  kind = button.dataset.kind; offset = 0; refreshVersion++;
  $('#search').value = ''; $('#status-filter').value = '';
  document.querySelectorAll('[data-kind]').forEach(el => el.classList.toggle('active', el === button));
  const isZ = kind === 'z-reports';
  $('#page-title').textContent = $('#breadcrumb').textContent = isZ ? 'Z Raporları' : 'Fişler';
  $('#export-label').textContent = isZ ? 'Z raporu Excel indir' : 'Fiş Excel indir';
  $('#upload-title').textContent = isZ ? 'Z raporlarınızı buraya bırakın' : 'Fişlerinizi buraya bırakın';
  $('#direction-label').hidden = isZ;
  $('#export-button').disabled = true;
  setDocumentRows('');
  if (settings) renderSelected();
  await refresh(); await loadLegacy();
}));
$('#choose-files').addEventListener('click', () => $('#document-files').click());
$('#document-files').addEventListener('change', event => { addFiles(event.target.files); event.target.value = ''; });
const dropzone = $('#dropzone');
['dragenter','dragover'].forEach(name => dropzone.addEventListener(name, event => { event.preventDefault(); dropzone.classList.add('dragging'); }));
['dragleave','drop'].forEach(name => dropzone.addEventListener(name, event => { event.preventDefault(); dropzone.classList.remove('dragging'); }));
dropzone.addEventListener('drop', event => addFiles(event.dataTransfer.files));
function addFiles(files) {
  if (!settings?.selected) { openSettings(); return; }
  for (const file of files) uploadItems.push({id:++uploadSequence, file, kind, program:settings.selected.program, chart_id:settings.selected.chart_id || '', direction:kind === 'z-reports' ? 'income' : $('#direction').value, status:'waiting', error:''});
  void uploadAll();
}
function renderUploads() {
  const complete = uploadItems.filter(item => item.status !== 'waiting' && item.status !== 'uploading').length;
  $('#upload-progress').hidden = !uploading;
  $('#upload-message').textContent = `${complete} / ${uploadItems.length} dosya yüklendi veya kontrol edildi. Yükleme bitene kadar bu sekmeyi açık tutun.`;
  $('#upload-meter').value = uploadItems.length ? complete * 100 / uploadItems.length : 0;
  const failed = uploadItems.filter(item => item.status === 'failed');
  $('#upload-errors').hidden = failed.length === 0;
  $('#upload-error-list').innerHTML = failed.map(item => `<li>${escape(item.file.name)} — ${escape(item.error)}</li>`).join('');
  $('#retry-uploads').disabled = uploading;
}
async function uploadAll() {
  if (uploading) return;
  uploading = true; $('#export-button').disabled = true;
  renderUploads();
  try {
    for (const item of uploadItems) {
      if (item.status !== 'waiting') continue;
      item.status = 'uploading'; renderUploads();
      try {
        if (item.file.size > 20 * 1024 * 1024) throw new Error('Dosya 20 MB sınırını aşıyor.');
        const body = new FormData(); body.append('file', item.file); body.append('kind', item.kind); body.append('direction', item.direction); body.append('program', item.program); body.append('chart_id', item.chart_id);
        const data = await jsonApi('/api/documents', {method:'POST', body});
        item.status = 'done';
        item.duplicate = data.items.every(doc => doc.duplicate);
        item.file = null;
      } catch (error) { item.status = 'failed'; item.error = error.message; }
      renderUploads();
    }
    const duplicates = uploadItems.filter(item => item.duplicate).length;
    toast(`Dosya yüklemesi tamamlandı.${duplicates ? ` ${duplicates} dosya zaten kayıtlı.` : ''} Belgeler sırayla okunuyor.`);
  } finally {
    uploading = false; renderUploads();
    uploadItems = uploadItems.filter(item => item.status === 'failed');
    await refresh();
  }
}
$('#retry-uploads').addEventListener('click', () => { uploadItems.forEach(item => { if (item.status === 'failed') item.status = 'waiting'; }); void uploadAll(); });
window.addEventListener('beforeunload', event => { if (uploading) { event.preventDefault(); event.returnValue = ''; } });

$('#document-table').addEventListener('click', async event => {
  const retry = event.target.closest('[data-retry]');
  const detail = event.target.closest('[data-detail]');
  if (retry) await retryDocument(retry.dataset.retry, retry);
  if (detail) await showDetail(detail.dataset.detail);
});
async function retryDocument(id, button) {
  button.disabled = true;
  try { await jsonApi(`/api/documents/${id}/retry`, {method:'POST'}); toast('Belge yeniden okuma sırasına alındı.'); await refresh(); }
  catch (error) { toast(error.message); button.disabled = false; }
}
function readingNotice(doc) {
  const data = doc.result || {}, issues = [doc.error, ...(data.issues || [])].filter(Boolean);
  if (!issues.length) return '';
  const partial = doc.status === 'review' && data.raw_text;
  const guidance = partial && !data.seller_name
    ? 'Firma unvanı dahil belgenin üst kısmının tamamını gösteren fotoğrafı yükleyin. Aynı kesilmiş fotoğrafı yeniden denemek eksik kısmı tamamlamaz.'
    : 'İşaretlenen alanları kaynak belgeyle karşılaştırın; daha net bir fotoğrafı dosya ekle alanından yükleyebilirsiniz.';
  return `<div class="detail-issues reading-notice"><b>${partial ? 'Belge kısmen okundu' : 'Belgeyi kontrol edin'}</b>${partial ? '<p>Okunan bilgiler aşağıda görünüyor. Eksik veya çelişen alanlar nedeniyle bu belge Excel’e aktarılmıyor.</p>' : ''}<ul>${issues.map(issue => `<li>${escape(issue)}</li>`).join('')}</ul><p>${guidance}</p></div>`;
}
async function showDetail(id) {
  const version = ++detailVersion;
  try {
    const doc = await jsonApi(`/api/documents/${id}`), data = doc.result;
    if (version !== detailVersion) return;
    const switchBtn = ['queued', 'processing'].includes(doc.status) ? '' : `<button id="detail-switch-kind" class="button secondary" title="Belge türünü ${doc.kind === 'receipts' ? 'Z Raporu' : 'Fiş'} olarak değiştir">⇄ ${doc.kind === 'receipts' ? 'Z Raporuna Taşı' : 'Fişe Taşı'}</button>`;
    $('#detail-title').textContent = doc.filename;
    $('#detail-body').innerHTML = `<div class="detail-grid"><div><div class="source-preview" id="source-preview">Kaynak belge yükleniyor…</div><a id="source-download" class="source-link" hidden>Kaynak dosyayı indir ↗</a></div><div>${badge(doc.status)}${readingNotice(doc)}<div class="detail-values">${fiscalDetail(doc)}</div>${data.vat_breakdown?.length ? `<table><thead><tr><th>Oran</th><th>Matrah</th><th>KDV</th></tr></thead><tbody>${data.vat_breakdown.map(part => `<tr><td>%${escape(part.rate)}</td><td>${escape(money(part.base))}</td><td>${escape(money(part.tax))}</td></tr>`).join('')}</tbody></table>` : ''}${accountingDetail(doc)}${doc.status === 'duplicate' ? '<p class="detail-issues">Aynı vergi kimliği, belge numarası, tarih ve tutarla bir kayıt zaten var. Bu kopya Excel’e eklenmez.</p>' : ''}${(data.notes || []).map(note => `<p class="muted">${escape(note)}</p>`).join('')}<details><summary>Okunan kaynak metin</summary><pre class="raw-text">${escape(data.raw_text || 'Henüz metin okunmadı.')}</pre></details><div style="display:flex;gap:8px;margin-top:12px;">${['review','failed','mapping'].includes(doc.status) ? '<button id="detail-retry" class="button secondary">↻ Yeniden dene</button>' : ''}${switchBtn}</div></div></div>`;
    if (!$('#detail-dialog').open) $('#detail-dialog').showModal();
    $('#detail-retry')?.addEventListener('click', async event => { await retryDocument(id, event.target); $('#detail-dialog').close(); });
    $('#detail-switch-kind')?.addEventListener('click', async event => {
      event.target.disabled = true;
      try {
        await jsonApi(`/api/documents/${id}/switch-kind`, {method:'POST'});
        toast('Belge türü değiştirildi ve yeniden okuma sırasına alındı.');
        $('#detail-dialog').close();
        await refresh();
      } catch (err) { toast(err.message); event.target.disabled = false; }
    });
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = null;
    const source = await api(`/api/documents/${id}/source`);
    const blob = await source.blob();
    if (!$('#detail-dialog').open || version !== detailVersion) return;
    previewUrl = URL.createObjectURL(blob);
    $('#source-preview').innerHTML = doc.filename.toLowerCase().endsWith('.pdf') ? `<iframe src="${previewUrl}#page=${doc.page + 1}" title="Kaynak PDF belgesi"></iframe>` : `<img src="${previewUrl}" alt="Kaynak belge">`;
    $('#source-download').href = previewUrl; $('#source-download').download = doc.filename; $('#source-download').hidden = false;
  } catch (error) { if (version !== detailVersion) return; if ($('#source-preview')) $('#source-preview').textContent = error.message; toast(error.message); }
}
$('#detail-dialog').addEventListener('close', () => { detailVersion++; if (previewUrl) URL.revokeObjectURL(previewUrl); previewUrl = null; });
function fiscalDetail(doc) {
  const data = doc.result, isZ = doc.kind === 'z-reports';
  const values = [[isZ ? 'Z raporu numarası' : 'Fiş / belge numarası', data.document_no],
    ['İşletme adı / unvanı', data.seller_name], ['Vergi dairesi', data.tax_office], ['VKN / TCKN', data.tax_id],
    ...(data.field_sources?.seller_name ? [['Firma adı kaynağı', `Aynı VKN / TCKN ile otomatik tamamlandı: ${data.field_sources.seller_name.filename}`]] : []),
    ['Tarih', data.document_datetime ? date(data.document_datetime) : ''], ['Saat', data.document_time],
    ...(isZ ? [['Mali sicil numarası', data.fiscal_id], ['Cihaz numarası', data.device_no], ['Fiş / işlem adedi', data.transaction_count],
      ['Kümülatif satış', money(data.cumulative_sales)], ['Kümülatif KDV', money(data.cumulative_vat)]] : [['Ürünler / açıklama', data.product_name]]),
    ['Genel toplam', money(data.total_amount)], ['Toplam KDV', money(data.vat_amount)],
    ['Okuma motoru', data.engine]];
  return values.map(([label,value]) => `<div><span>${escape(label)}</span><b>${escape(value === '' || value == null ? 'Belgede belirtilmemiş / okunamadı' : value)}</b></div>`).join('');
}
function lineDetails(doc) {
  const data = doc.result;
  const number = value => value === '' || value == null ? '—' : new Intl.NumberFormat('tr-TR',{maximumFractionDigits:6}).format(Number(value));
  const items = data.items?.length ? `<h3>Ürün / hizmet kalemleri</h3><div class="table-wrap"><table class="item-detail-table"><thead><tr><th>Ürün / hizmet adı</th><th>Miktar</th><th>Birim</th><th>Birim fiyat</th><th>Kalem tutarı</th><th>KDV</th></tr></thead><tbody>${data.items.map(item => `<tr><td>${escape(item.name)}</td><td>${escape(number(item.quantity))}</td><td>${escape(item.unit || '—')}</td><td>${escape(money(item.unit_price))}</td><td>${escape(money(item.amount))}</td><td>${item.rate == null ? '—' : '%' + escape(item.rate)}</td></tr>`).join('')}</tbody></table></div>` : '';
  const labels = {discount:'İndirim',cancellation:'İptal',refund:'İade'};
  const adjustmentRows = Object.entries(data.adjustments || {});
  const adjustments = `<h3>İndirim, iptal ve iade</h3>${adjustmentRows.length ? `<table><thead><tr><th>Tür</th><th>Tutar</th><th>Adet</th></tr></thead><tbody>${adjustmentRows.map(([key,value]) => `<tr><td>${escape(labels[key] || key)}</td><td>${escape(money(value.amount))}</td><td>${escape(number(value.count))}</td></tr>`).join('')}</tbody></table>` : '<p class="muted">Belgede bu alanlar belirtilmemiş.</p>'}`;
  return items + adjustments;
}
function accountingDetail(doc) {
  const data = doc.result, evidence = data.bank_evidence || {};
  const payments = data.payment_entries || [];
  const bankNames = (evidence.bank_codes || []).map(code => settings.banks.find(bank => bank.code === code)?.name || code);
  const paymentTable = payments.length ? `<h3>Ödeme dağılımı</h3><table><thead><tr><th>Ödeme</th><th>Tutar</th></tr></thead><tbody>${payments.map(payment => `<tr><td>${escape(settings.payment_labels[payment.method] || payment.method)}${payment.provider ? `<small>${escape(payment.provider)}</small>` : ''}${payment.bank_code ? `<small>${escape(settings.banks.find(bank => bank.code === payment.bank_code)?.name || payment.bank_code)}</small>` : ''}</td><td>${escape(money(payment.amount))}</td></tr>`).join('')}</tbody></table>` : '';
  const bankText = bankNames.length || evidence.card_last4?.length ? `<p class="muted">Belgedeki banka: ${escape(bankNames.join(', ') || 'Belirtilmemiş')}${evidence.card_last4?.length ? ` · Kart son 4 hane: ${escape(evidence.card_last4.join(', '))}` : ''}</p>` : '';
  const journal = doc.accounting?.rows ? `<h3>Otomatik muhasebe fişi</h3><p class="muted">${doc.accounting.status === 'matched' ? 'Firma hesap planıyla eşleştirildi.' : 'Standart ana hesaplarla hazırlandı.'}</p><table><thead><tr><th>Hesap</th><th>Borç</th><th>Alacak</th></tr></thead><tbody>${doc.accounting.rows.map(row => `<tr><td><b>${escape(row.account)}</b><small>${escape(row.name)}</small></td><td>${escape(money(row.debit))}</td><td>${escape(money(row.credit))}</td></tr>`).join('')}</tbody></table>` : '';
  return lineDetails(doc) + paymentTable + bankText + journal + (doc.status === 'mapping' ? '<p class="notice">Hesap planını program ayarlarından güncellediğinizde eşleşmeler otomatik yeniden hesaplanır.</p>' : '');
}
document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => $(`#${button.dataset.close}`).close()));
$('#previous-page').addEventListener('click', () => { offset = Math.max(0, offset - 50); refreshVersion++; void refresh(); });
$('#next-page').addEventListener('click', () => { offset += 50; refreshVersion++; void refresh(); });
let searchTimer;
$('#search').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { offset = 0; refreshVersion++; void refresh(); }, 250); });
$('#status-filter').addEventListener('change', () => { offset = 0; refreshVersion++; void refresh(); });
$('#export-button').addEventListener('click', async () => {
  const button = $('#export-button'); button.disabled = true;
  const context = scopeValues(), exportKind = kind;
  try {
    const mapped = usesCustomTemplate();
    const response = await api(`/export/excel?${new URLSearchParams({...context, type:exportKind, mapped:mapped ? '1' : '0'})}`);
    const blob = await response.blob(), url = URL.createObjectURL(blob), link = document.createElement('a');
    link.href = url; link.download = `${context.program}_${exportKind === 'receipts' ? 'fisler' : 'z_raporlari'}${context.period ? '_' + context.period : ''}.xlsx`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast(mapped ? 'Eşleştirdiğiniz sütunlarla Excel hazırlandı.' : 'Örnek sütun düzeniyle Excel hazırlandı.');
  } catch (error) { toast(error.message); }
  finally { await refresh(); }
});

$('#clear-all-button')?.addEventListener('click', async () => {
  if (!confirm('Tüm kayıtlı belgelerinizi silmek istediğinizden emin misiniz?')) return;
  const button = $('#clear-all-button');
  button.disabled = true;
  try {
    const res = await jsonApi('/api/documents', { method: 'DELETE' });
    toast(res.message || 'Tüm belgeler silindi.');
    await refresh();
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
});

async function openSettings() {
  if (uploading) { toast('Firma veya program değiştirmek için yüklemenin tamamlanmasını bekleyin.'); return; }
  if (!settings) {
    try {
      settings = await jsonApi('/api/settings');
      if ($('#program-select')) {
        $('#program-select').innerHTML = '<option value="">Program seçin</option>' + settings.programs.map(p => `<option value="${escape(p.id)}">${escape(p.name)}</option>`).join('');
      }
      renderSelected();
    } catch (e) {
      toast('Program ayarları yüklenemedi. Sayfayı yenileyin.');
      return;
    }
  }
  $('#program-select').value = settings.selected?.program || '';
  loadDraft($('#program-select').value);
  $('#settings-error').textContent = '';
  if (!$('#settings-dialog').open) $('#settings-dialog').showModal();
}
$('#settings-button').addEventListener('click', () => void openSettings());
$('#setup-open').addEventListener('click', () => void openSettings());
$('#program-select').addEventListener('change', event => loadDraft(event.target.value));
function loadDraft(program) {
  draft = structuredClone(settings.profiles[program] || settings.defaults[program] || settings.defaults.custom);
  draft.program = program;
  $('#program-version').value = draft.version;
  $('#automatic-accounts').hidden = !program;
  $('#account-selection-hint').hidden = Boolean(program);
  renderChartSettings();
  renderMapping();
}
function renderChartSettings() {
  const charts = (settings.charts || []).filter(chart => chart.program === draft.program);
  $('#company-plan-select').innerHTML = '<option value="">Standart hesaplar / önceki belgeler</option>' + charts.map(chart => `<option value="${escape(chart.id)}">${escape(chart.name)}</option>`).join('');
  $('#company-plan-select').value = draft.chart_id || '';
  const chart = charts.find(item => item.id === draft.chart_id);
  $('#update-chart').hidden = !chart;
  $('#new-chart').disabled = !draft.program;
  $('#account-plan-name').textContent = chart ? `${chart.name} · ${chart.account_count} hesap` : draft.account_plan.name;
  $('#account-plan-explanation').textContent = chart ? 'Banka ve kart hesapları kaynak hesap planından alındı. Fişler okununca gider, KDV ve ödeme hesapları otomatik eşleştirilir. Belirsiz eşleşmeler incelemeye ayrılır.' : 'Standart ana hesaplar kullanılır. Firmanın alt hesaplarını otomatik almak için hesap planını yükleyin.';
  $('#account-list').innerHTML = chart ? chart.bank_accounts.map(account => `<div><dt>${escape(account.name)}${account.iban_masked ? `<small>${escape(account.iban_masked)}</small>` : ''}</dt><dd><strong>${escape(account.code)}</strong><span>${escape(account.bank_name)}${account.bank_code ? ` · EFT ${escape(account.bank_code)}` : ''}${account.card_last4 ? ` · Kart ${escape(account.card_last4)}` : ''}</span></dd></div>`).join('') || '<div><dt>Bu planda banka veya kart alt hesabı bulunamadı.</dt><dd>Kaynak hesap planını güncelleyebilirsiniz.</dd></div>' : draft.program ? Object.entries(settings.account_labels).map(([key,label]) => `<div><dt>${escape(label)}</dt><dd><strong>${escape(draft.accounts[key])}</strong><span>${escape(settings.account_names[draft.accounts[key]] || '')}</span></dd></div>`).join('') : '';
}
$('#company-plan-select').addEventListener('change', event => { draft.chart_id = event.target.value; renderChartSettings(); });
$('#new-chart').addEventListener('click', () => { chartImportMode = 'new'; $('#chart-file').click(); });
$('#update-chart').addEventListener('click', () => { chartImportMode = 'update'; $('#chart-file').click(); });
$('#chart-file').addEventListener('change', async event => {
  const file = event.target.files[0]; event.target.value = ''; if (!file) return;
  if (!draft.program) { $('#settings-error').textContent = 'Önce muhasebe programını seçin.'; return; }
  const program = draft.program, chartId = chartImportMode === 'update' ? draft.chart_id : '';
  $('#new-chart').disabled = $('#update-chart').disabled = $('#program-select').disabled = true;
  $('#chart-status').textContent = 'Hesap planı okunuyor; banka ve hesap kodları eşleştiriliyor…';
  $('#settings-error').textContent = '';
  try {
    const body = new FormData(); body.append('file', file); body.append('program', program); body.append('chart_id', chartId);
    const response = await jsonApi('/api/account-charts', {method:'POST',body});
    settings = await jsonApi('/api/settings');
    offset = 0; refreshVersion++; $('#period-filter').value = '';
    $('#program-select').value = program; loadDraft(program); renderSelected();
    $('#chart-status').textContent = `${response.chart.account_count} hesap alındı. ${response.chart.bank_accounts.length} banka / kart hesabı hazır. Firma seçimi kaydedildi.`;
    await refresh(); await loadLegacy();
  } catch (error) { $('#settings-error').textContent = error.message; $('#chart-status').textContent = 'Hesap planı yüklenemedi. Mevcut kayıtlar korundu.'; }
  finally { $('#new-chart').disabled = $('#update-chart').disabled = $('#program-select').disabled = false; }
});
$('#workspace-chart').addEventListener('change', async event => {
  if (uploading) { event.target.value = settings.selected?.chart_id || ''; toast('Yükleme bitince firma değiştirebilirsiniz.'); return; }
  const chartId = event.target.value;
  try {
    const response = await jsonApi('/api/settings', {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({...settings.selected, chart_id:chartId})});
    settings.selected = response.profile; settings.profiles[response.profile.program] = response.profile;
    offset = 0; refreshVersion++; $('#period-filter').value = ''; $('#search').value = ''; $('#status-filter').value = '';
    renderSelected(); await refresh(); await loadLegacy();
  } catch (error) { event.target.value = settings.selected?.chart_id || ''; toast(error.message); }
});
$('#period-filter').addEventListener('change', () => { offset = 0; refreshVersion++; void refresh(); });
function renderMapping() {
  $('#template-filename').textContent = draft.template_name;
  $('#template-badge').textContent = draft.template_name ? 'Şablon eşleştirildi' : 'İsteğe bağlı';
  $('#template-options').hidden = !draft.template_name;
  $('#mapping-table').hidden = !draft.template_name;
  $('#reset-template').hidden = !draft.template_name;
  $('#template-kind').value = draft.template_kind || 'receipts';
  $('#sheet-name').value = draft.sheet_name; $('#header-row').value = draft.header_row; $('#date-format').value = draft.date_format;
  $('#mapping-table').innerHTML = draft.columns.map((column,index) => `<div class="mapping-row"><span>${index + 1}. ${escape(column.header)}</span><select data-map="${index}" aria-label="${escape(column.header)} alan eşleştirmesi">${Object.entries(settings.fields).map(([key,label]) => `<option value="${key}" ${key === column.field ? 'selected' : ''}>${escape(label)}</option>`).join('')}</select><input data-constant="${index}" placeholder="Sabit değer" aria-label="${escape(column.header)} sabit değeri" value="${escape(column.value || '')}" ${column.field === 'constant' ? '' : 'hidden'}></div>`).join('');
}
$('#mapping-table').addEventListener('change', event => {
  if (event.target.dataset.map != null) {
    const index = Number(event.target.dataset.map); draft.columns[index].field = event.target.value;
    $(`[data-constant="${index}"]`).hidden = event.target.value !== 'constant';
  }
});
$('#template-file').addEventListener('change', async event => {
  const file = event.target.files[0]; event.target.value = ''; if (!file) return;
  $('#settings-error').textContent = '';
  try { const body = new FormData(); body.append('file', file); Object.assign(draft, await jsonApi('/api/template', {method:'POST',body})); renderMapping(); }
  catch (error) { $('#settings-error').textContent = error.message; }
});
$('#reset-template').addEventListener('click', () => { draft.columns = structuredClone(settings.defaults.custom.columns); draft.template_name = ''; draft.sheet_name = 'Muhasebe Fişleri'; draft.header_row = 1; renderMapping(); });
$('#settings-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true; $('#settings-error').textContent = '';
  try {
    draft.program = $('#program-select').value; draft.version = $('#program-version').value; draft.chart_id = $('#company-plan-select').value;
    draft.template_kind = $('#template-kind').value;
    document.querySelectorAll('[data-constant]').forEach(el => { draft.columns[Number(el.dataset.constant)].value = el.value; });
    draft.sheet_name = $('#sheet-name').value; draft.header_row = Number($('#header-row').value); draft.date_format = $('#date-format').value;
    const data = await jsonApi('/api/settings', {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({...draft, accounts:undefined, account_plan:undefined})});
    settings.selected = data.profile; settings.profiles[data.profile.program] = data.profile;
    offset = 0; refreshVersion++; $('#period-filter').value = '';
    renderSelected(); $('#settings-dialog').close(); toast('Program seçildi, hesap kodları otomatik yüklendi.'); await refresh();
  } catch (error) { $('#settings-error').textContent = error.message; }
  finally { button.disabled = false; }
});

const authToggle = $('#auth-toggle') || $('#auth-mode-toggle');
if (authToggle) {
  authToggle.addEventListener('click', () => {
    registerMode = !registerMode;
    const nameField = $('#name-field') || $('#register-fields');
    if (nameField) nameField.hidden = !registerMode;
    const loginName = $('#login-name') || $('#register-name');
    if (loginName) loginName.required = registerMode;
    const loginSubmit = $('#login-submit');
    if (loginSubmit) loginSubmit.textContent = registerMode ? 'Hesap oluştur →' : 'Giriş yap →';
    authToggle.textContent = registerMode ? 'Zaten hesabım var · Giriş yap' : 'İlk kez kullanıyorum · Hesap oluştur';
    const loginPassword = $('#login-password');
    if (loginPassword) loginPassword.autocomplete = registerMode ? 'new-password' : 'current-password';
  });
}

const loginForm = $('#login-form');
if (loginForm) {
  loginForm.addEventListener('submit', async event => {
    event.preventDefault();
    const errorEl = $('#login-error');
    if (errorEl) errorEl.textContent = '';
    const button = $('#login-submit');
    if (button) button.disabled = true;
    try {
      if (!auth) throw new Error('Giriş servisi yüklenemedi. İnternet bağlantısını kontrol edip sayfayı yenileyin.');
      const tc = $('#login-tc')?.value.trim() || '';
      const password = $('#login-password')?.value || '';
      if (!/^[1-9][0-9]{10}$/.test(tc)) throw new Error('11 haneli geçerli kimlik numarası girin.');
      if (registerMode) {
        const credential = await authApi.createUserWithEmailAndPassword(auth, `${tc}@celikel-smm.local`, password);
        const nameVal = ($('#login-name') || $('#register-name'))?.value.trim() || '';
        await authApi.updateProfile(credential.user, {displayName: nameVal});
        if ($('#user-name')) $('#user-name').textContent = credential.user.displayName;
      } else {
        await authApi.signInWithEmailAndPassword(auth, `${tc}@celikel-smm.local`, password);
      }
    } catch (error) {
      if (errorEl) {
        errorEl.textContent = error.code === 'auth/email-already-in-use' ? 'Bu kimlik numarasıyla bir hesap zaten var.' : error.code ? 'Giriş tamamlanamadı. Bilgilerinizi ve internet bağlantınızı kontrol edin.' : error.message;
      }
    } finally {
      if (button) button.disabled = false;
    }
  });
}
$('#logout').addEventListener('click', async () => {
  if (uploading) { toast('Çıkış yapmadan önce dosya yüklemesinin tamamlanmasını bekleyin.'); return; }
  if (localAuth) {
    window.location.href = '/';
    return;
  }
  try {
    if (authApi && auth) await authApi.signOut(auth);
    window.location.href = '/';
  } catch (error) {
    window.location.href = '/';
  }
});
if (localAuth) await begin();
else {
  try {
    const [{initializeApp}, apiModule, {firebaseConfig}] = await Promise.all([
      import('https://www.gstatic.com/firebasejs/10.12.5/firebase-app.js'),
      import('https://www.gstatic.com/firebasejs/10.12.5/firebase-auth.js'),
      import('/firebase-config.js')
    ]);
    authApi = apiModule; auth = authApi.getAuth(initializeApp(firebaseConfig));
    authApi.onAuthStateChanged(auth, async user => {
      refreshVersion++;
      authUser = user;
      if (user) await begin();
      else {
        signedIn = false;
        clearTimeout(pollTimer);
        const ws = $('#workspace');
        if (ws) {
          ws.hidden = true;
          ws.style.display = 'none';
        }
        const ls = $('#login-screen') || $('#landing-screen');
        if (ls) {
          ls.hidden = false;
          ls.style.display = '';
        }
        document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close());
        const dt = $('#document-table');
        if (dt) dt.innerHTML = '';
        const lt = $('#legacy-table');
        if (lt) lt.innerHTML = '';
        settings = null;
        uploadItems = [];
      }
    });
  } catch (error) { $('#login-error').textContent = 'Giriş servisi yüklenemedi. İnternet bağlantısını kontrol edip sayfayı yenileyin.'; }
}
