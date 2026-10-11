/**
 * 請求書 自動確認・ダウンロード（Google Apps Script）
 *
 * - Gmail から請求書らしきメールを探し、添付ファイルを Google ドライブの月別フォルダへ保存
 * - クラウド請求書サービスの通知メールは、PDF への直リンクなら自動ダウンロード、
 *   ログインが必要なものは「要手動DL」としてリンクを一覧に記録
 * - 毎月の取引先リストと照合し、「まだ届いていない請求書」をメールで知らせる
 *
 * 初回は setup() を一度だけ手動実行してください。
 */

// ===== 設定 =====
const CONFIG = {
  // ドライブに作る保存先フォルダ名（この下に YYYY-MM フォルダができる）
  ROOT_FOLDER_NAME: '請求書',
  // 管理用スプレッドシート名
  SPREADSHEET_NAME: '請求書管理',
  // 処理したスレッドに付ける Gmail ラベル
  PROCESSED_LABEL: '請求書/自動取込済み',

  // 請求書メールの検索条件（Gmail の検索演算子がそのまま使えます）
  SEARCH_QUERY:
    '(請求書 OR 御請求書 OR ご請求 OR 請求明細 OR 支払通知 OR invoice OR 領収書) ' +
    '-from:me newer_than:40d',
  // 1 回の実行で見るスレッド数の上限（実行時間 6 分制限対策）
  MAX_THREADS: 100,

  // 保存する添付ファイルの拡張子
  ATTACHMENT_EXTENSIONS: ['pdf', 'xlsx', 'xls', 'csv', 'zip'],

  // クラウド請求書サービスの送信元（From に含まれる文字列）。
  // 実際に届いているメールの送信元アドレスを見て追加・修正してください。
  CLOUD_SERVICES: [
    { name: 'Bill One', match: 'bill-one' },
    { name: '楽楽明細', match: 'rakurakumeisai' },
    { name: 'BtoBプラットフォーム', match: 'infomart' },
    { name: 'マネーフォワード', match: 'moneyforward' },
    { name: 'freee', match: 'freee' },
    { name: 'Misoca', match: 'misoca' },
    { name: 'MakeLeaps', match: 'makeleaps' },
  ],

  // 月次レポートを送る日（毎月この日の朝 9 時）と送信先（空なら自分）
  REPORT_DAY: 5,
  REPORT_TO: '',
};

const SHEET_LOG = '受信一覧';
const SHEET_VENDORS = '取引先リスト';
const LOG_HEADER = ['取込日時', '受信日', '対象月', '送信元', '件名', '種別', '状態', 'ファイル/リンク', 'メッセージID'];
const VENDOR_HEADER = ['取引先名', '検索キー（メールアドレス・ドメイン・件名の一部）', '備考'];

// ===== 初期設定 =====

/** 初回に一度だけ実行：スプレッドシート・フォルダ・ラベル・トリガーを作成 */
function setup() {
  getSpreadsheet_();
  getRootFolder_();
  getOrCreateLabel_(CONFIG.PROCESSED_LABEL);

  ScriptApp.getProjectTriggers().forEach(t => {
    if (['processInvoices', 'sendMonthlyReport'].includes(t.getHandlerFunction())) {
      ScriptApp.deleteTrigger(t);
    }
  });
  ScriptApp.newTrigger('processInvoices').timeBased().everyDays(1).atHour(7).create();
  ScriptApp.newTrigger('sendMonthlyReport').timeBased().onMonthDay(CONFIG.REPORT_DAY).atHour(9).create();

  processInvoices();
  Logger.log('セットアップ完了: ' + getSpreadsheet_().getUrl());
}

// ===== メイン処理 =====

/** 請求書メールを探して添付を保存し、一覧に記録する（毎日自動実行） */
function processInvoices() {
  const ss = getSpreadsheet_();
  const logSheet = ss.getSheetByName(SHEET_LOG);
  const doneIds = new Set(
    logSheet.getLastRow() > 1
      ? logSheet.getRange(2, LOG_HEADER.length, logSheet.getLastRow() - 1, 1).getValues().flat()
      : []
  );
  const label = getOrCreateLabel_(CONFIG.PROCESSED_LABEL);
  const tz = Session.getScriptTimeZone();
  const rows = [];

  const threads = GmailApp.search(CONFIG.SEARCH_QUERY, 0, CONFIG.MAX_THREADS);
  threads.forEach(thread => {
    thread.getMessages().forEach(msg => {
      const id = msg.getId();
      if (doneIds.has(id)) return;

      const date = msg.getDate();
      const month = Utilities.formatDate(date, tz, 'yyyy-MM');
      const base = [
        new Date(),
        Utilities.formatDate(date, tz, 'yyyy-MM-dd'),
        month,
        msg.getFrom(),
        msg.getSubject(),
      ];
      const service = findCloudService_(msg.getFrom());
      const kind = service ? service.name : 'メール添付';
      let found = false;

      // 1) 添付ファイルを保存
      msg.getAttachments({ includeInlineImages: false }).forEach(att => {
        if (!hasTargetExtension_(att.getName())) return;
        const file = saveBlob_(att.copyBlob(), att.getName(), date, msg.getFrom(), month);
        rows.push(base.concat([kind, '保存済み', file.getUrl(), id]));
        found = true;
      });

      // 2) クラウドサービスの通知メールならリンクを処理
      if (service) {
        extractLinks_(msg).forEach(url => {
          const file = tryDownloadPdf_(url, date, msg.getFrom(), month);
          if (file) {
            rows.push(base.concat([kind, '保存済み（リンクから取得）', file.getUrl(), id]));
          } else {
            rows.push(base.concat([kind, '要手動DL', url, id]));
          }
          found = true;
        });
      }

      // 添付もリンクもない請求書メールも、見落とし防止のため記録
      if (!found) {
        rows.push(base.concat([kind, '要確認（添付なし）', threadUrl_(thread), id]));
      }
      doneIds.add(id);
    });
    thread.addLabel(label);
  });

  if (rows.length) {
    logSheet.getRange(logSheet.getLastRow() + 1, 1, rows.length, LOG_HEADER.length).setValues(rows);
  }
  Logger.log(rows.length + ' 件を記録しました');
}

/** 今月届いた請求書と、取引先リストのうち未着のものをメールで知らせる */
function sendMonthlyReport() {
  processInvoices();

  const ss = getSpreadsheet_();
  const tz = Session.getScriptTimeZone();
  const thisMonth = Utilities.formatDate(new Date(), tz, 'yyyy-MM');
  const logs = ss.getSheetByName(SHEET_LOG).getDataRange().getValues().slice(1)
    .filter(r => r[2] === thisMonth);

  const vendors = ss.getSheetByName(SHEET_VENDORS).getDataRange().getValues().slice(1)
    .filter(r => r[0] && r[1]);
  const missing = vendors.filter(v => {
    const key = String(v[1]).toLowerCase();
    return !logs.some(r => (r[3] + ' ' + r[4]).toLowerCase().includes(key));
  });
  const manual = logs.filter(r => String(r[6]).startsWith('要'));

  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const list = (items, fmt) => items.length ? '<ul>' + items.map(i => '<li>' + fmt(i) + '</li>').join('') + '</ul>' : '<p>なし</p>';
  const link = (url, text) => '<a href="' + esc(url) + '">' + esc(text) + '</a>';

  const html =
    '<h3>' + thisMonth + ' の請求書チェック</h3>' +
    '<p>受信 ' + logs.length + ' 件 / 要対応 ' + manual.length + ' 件 / 未着 ' + missing.length + ' 社</p>' +
    '<h4>未着の取引先</h4>' + list(missing, v => esc(v[0])) +
    '<h4>手動でダウンロード・確認が必要なもの</h4>' +
    list(manual, r => esc(r[1]) + ' ' + esc(r[5]) + '：' + link(r[7], r[4]) + '（' + esc(r[6]) + '）') +
    '<h4>保存済み</h4>' +
    list(logs.filter(r => !String(r[6]).startsWith('要')), r => esc(r[1]) + ' ' + link(r[7], r[4])) +
    '<p>' + link(ss.getUrl(), '管理スプレッドシートを開く') + '　' +
    link(getMonthFolder_(thisMonth).getUrl(), '今月の保存フォルダを開く') + '</p>';

  MailApp.sendEmail({
    to: CONFIG.REPORT_TO || Session.getEffectiveUser().getEmail(),
    subject: '【請求書チェック】' + thisMonth + '：未着 ' + missing.length + ' 社 / 要対応 ' + manual.length + ' 件',
    htmlBody: html,
  });
}

// ===== 補助関数 =====

function findCloudService_(from) {
  const f = from.toLowerCase();
  return CONFIG.CLOUD_SERVICES.find(s => f.includes(s.match.toLowerCase())) || null;
}

function hasTargetExtension_(name) {
  const ext = String(name).split('.').pop().toLowerCase();
  return CONFIG.ATTACHMENT_EXTENSIONS.includes(ext);
}

/** 本文から請求書ページらしいリンクを抜き出す（配信停止・ヘルプ等は除外） */
function extractLinks_(msg) {
  const text = msg.getPlainBody() || '';
  const urls = text.match(/https?:\/\/[^\s<>"'）)」]+/g) || [];
  const ignore = /(unsubscribe|optout|opt-out|privacy|policy|help|support|faq|terms|配信停止)/i;
  return [...new Set(urls)].filter(u => !ignore.test(u)).slice(0, 5);
}

/**
 * PDF への直リンクだけダウンロードする。
 * ログイン画面などの HTML が返ってきた場合は保存しない（＝要手動DL）。
 * 「受領確認」等の操作リンクを誤って踏まないよう、GET で .pdf を含む URL のみ対象。
 */
function tryDownloadPdf_(url, date, from, month) {
  if (!/\.pdf(\?|$)/i.test(url)) return null;
  try {
    const res = UrlFetchApp.fetch(url, { muteHttpExceptions: true, followRedirects: true });
    const type = String(res.getHeaders()['Content-Type'] || res.getHeaders()['content-type'] || '');
    if (res.getResponseCode() !== 200 || !type.includes('pdf')) return null;
    const name = decodeURIComponent(url.split('?')[0].split('/').pop()) || 'invoice.pdf';
    return saveBlob_(res.getBlob(), name, date, from, month);
  } catch (e) {
    Logger.log('ダウンロード失敗: ' + url + ' ' + e);
    return null;
  }
}

/** 「受信日_送信者_元ファイル名」で月別フォルダに保存 */
function saveBlob_(blob, originalName, date, from, month) {
  const tz = Session.getScriptTimeZone();
  const sender = (from.replace(/<.*>/, '').replace(/["']/g, '').trim() || from)
    .replace(/[\\/:*?"<>|]/g, '_').slice(0, 30);
  const name = Utilities.formatDate(date, tz, 'yyyyMMdd') + '_' + sender + '_' + originalName;
  return getMonthFolder_(month).createFile(blob).setName(name);
}

function getMonthFolder_(month) {
  const root = getRootFolder_();
  const it = root.getFoldersByName(month);
  return it.hasNext() ? it.next() : root.createFolder(month);
}

function getRootFolder_() {
  const props = PropertiesService.getScriptProperties();
  const id = props.getProperty('ROOT_FOLDER_ID');
  if (id) {
    try { return DriveApp.getFolderById(id); } catch (e) { /* 削除されていたら作り直す */ }
  }
  const folder = DriveApp.createFolder(CONFIG.ROOT_FOLDER_NAME);
  props.setProperty('ROOT_FOLDER_ID', folder.getId());
  return folder;
}

function getSpreadsheet_() {
  const props = PropertiesService.getScriptProperties();
  const id = props.getProperty('SPREADSHEET_ID');
  if (id) {
    try { return SpreadsheetApp.openById(id); } catch (e) { /* 削除されていたら作り直す */ }
  }
  const ss = SpreadsheetApp.create(CONFIG.SPREADSHEET_NAME);
  const log = ss.getSheets()[0].setName(SHEET_LOG);
  log.appendRow(LOG_HEADER);
  log.setFrozenRows(1);
  const vendors = ss.insertSheet(SHEET_VENDORS);
  vendors.appendRow(VENDOR_HEADER);
  vendors.appendRow(['（例）〇〇株式会社', 'example.co.jp', '毎月末締め・翌月3日頃に届く']);
  vendors.setFrozenRows(1);
  props.setProperty('SPREADSHEET_ID', ss.getId());
  return ss;
}

function getOrCreateLabel_(name) {
  return GmailApp.getUserLabelByName(name) || GmailApp.createLabel(name);
}

function threadUrl_(thread) {
  return 'https://mail.google.com/mail/u/0/#all/' + thread.getId();
}
