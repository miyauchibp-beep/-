/**
 * 宮内自動車 問い合わせGoogleフォーム 項目追加スクリプト（Google Apps Script）
 *
 * 目的：問い合わせの時点で「希望作業」「予算感」「症状」がわかるようにし、
 *       概算（金額の幅）で合意できた人だけを詳細見積もりに進める。
 *
 * 方針：
 * - 金額・自動見積もりはフォームに一切表示しない（追加するのは質問だけ）
 * - 既存の項目は削除・変更しない（追加のみ。オイル交換の説明文だけ追記）
 * - 何回実行しても重複しない（同じタイトルの質問があればスキップ）
 *
 * 使い方：
 * 1. まず testOnCopies を実行し、コピーされたテスト用フォームで確認する
 * 2. 問題なければ updateAllForms（または各フォームの関数）を実行する
 * 詳しくは forms/README.md を参照。
 */

// ===== 設定 =====

// 対象フォーム（編集用ID）
const FORM_IDS = {
  OIL: '1RT6da8uXF8E4oaDR7Kt7U2e7lQhNnkB9k1H5FcdNif4', // ① オイル交換 問い合わせ
  REPAIR: '1syPHmSGlvC_ybXFMbsI7-K2cqTUR5T0OQE4EIa_K0nM', // ② 整備 修理の問い合わせ
  BODY: '1c5hGuxOcBBDxVObVp3kYZaJOS50E_kujCHYVFVmPJCs', // ③ 鈑金塗装 問い合わせ
};

// ご予算の選択肢（区切り金額は仮。変更する場合はここを書き換えてから実行）
// ※ すでに追加済みの質問は上書きしません。追加後に変える場合はフォーム上で直接編集してください。
const BUDGET_CHOICES_OIL = ['〜3万円', '3万〜5万円', '5万〜10万円', '10万円以上', '概算を見てから判断したい'];
const BUDGET_CHOICES_REPAIR_BODY = ['〜5万円', '5万〜10万円', '10万〜30万円', '30万円以上', '概算を見てから判断したい'];

// 追加した質問を並べる基準になる既存の質問
const ANCHOR_TITLE = '問い合わせ内容';

// ① オイル交換の説明文：この文字列の直後に追記する
const OIL_DESCRIPTION_MARKER = 'で計算します';
const OIL_DESCRIPTION_ADDITION =
  'まず概算（金額の幅）でご返信し、\n' +
  'ご希望の方のみ詳細なお見積もりをお作りします。\n' +
  '金額は車両の状態により変わります。';

// 追加する質問のタイトル
const TITLES = {
  WORK: 'ご希望の作業を選んでください（複数可）',
  BUDGET: 'ご予算の目安を教えてください',
  SYMPTOM_OIL: '気になる症状はありますか',
  SYMPTOM_REPAIR: '症状について教えてください（いつ・どんな時に・どのくらいの頻度で）',
  INSURANCE: '保険を使う予定はありますか',
  CONFIRM: '確認事項',
};

const CONFIRM_TEXT_VEHICLE = '金額は車両の状態により変わり、入庫後の確認で確定することを了承します';
const CONFIRM_TEXT_BODY = '金額は実車確認で変わる場合があることを了承します';

// ===== 実行用の関数（エディタ上部の関数選択から選んで実行） =====

/** 3つのフォームすべてに追加する */
function updateAllForms() {
  updateOilForm();
  updateRepairForm();
  updateBodyForm();
}

/** ① オイル交換 問い合わせ */
function updateOilForm() {
  updateOilForm_(FORM_IDS.OIL);
}

/** ② 整備 修理の問い合わせ */
function updateRepairForm() {
  updateRepairForm_(FORM_IDS.REPAIR);
}

/** ③ 鈑金塗装 問い合わせ */
function updateBodyForm() {
  updateBodyForm_(FORM_IDS.BODY);
}

/**
 * 3つのフォームのコピーを作り、コピーにだけ項目を追加する（本番フォームは変更しない）。
 * ログに表示される編集URLを開いて、並び順・文言を確認してください。
 * 確認が終わったテスト用フォームはドライブから削除してください。
 */
function testOnCopies() {
  const updaters = { OIL: updateOilForm_, REPAIR: updateRepairForm_, BODY: updateBodyForm_ };
  Object.keys(FORM_IDS).forEach(function (key) {
    const original = DriveApp.getFileById(FORM_IDS[key]);
    const copy = original.makeCopy('【テスト】' + original.getName());
    updaters[key](copy.getId());
    Logger.log('テスト用フォーム（' + original.getName() + '）: ' + FormApp.openById(copy.getId()).getEditUrl());
  });
}

/** 3つのフォームの項目一覧（番号・種類・タイトル・必須）をログに出す。変更は何もしない */
function listAllFormItems() {
  Object.keys(FORM_IDS).forEach(function (key) {
    const form = FormApp.openById(FORM_IDS[key]);
    Logger.log('===== ' + form.getTitle() + ' =====');
    form.getItems().forEach(function (item) {
      Logger.log(item.getIndex() + ': [' + item.getType() + '] ' + item.getTitle() + (isRequired_(item) ? ' *' : ''));
    });
  });
}

// ===== フォームごとの追加内容 =====

function updateOilForm_(formId) {
  const form = FormApp.openById(formId);
  Logger.log('■ ' + form.getTitle());
  requireItem_(form, ANCHOR_TITLE);

  appendToDescription_(form, OIL_DESCRIPTION_MARKER, OIL_DESCRIPTION_ADDITION);

  insertBefore_(form, ANCHOR_TITLE, [
    {
      type: 'checkbox',
      title: TITLES.WORK,
      choices: [
        'ATF/CVTF交換（圧送式）',
        'オイルパン脱着洗浄・ストレーナー交換',
        'デフオイル交換（前後）',
        'トランスファーオイル交換',
        'エンジンオイル交換',
        'おまかせ・相談したい',
      ],
    },
    { type: 'radio', title: TITLES.BUDGET, choices: BUDGET_CHOICES_OIL },
    {
      type: 'radio',
      title: TITLES.SYMPTOM_OIL,
      choices: ['特になし（予防整備）', '異音', '変速時のショック・すべり', 'その他（問い合わせ内容欄に詳しく）'],
    },
  ]);

  addQuestion_(form, { type: 'checkbox', title: TITLES.CONFIRM, choices: [CONFIRM_TEXT_VEHICLE] });
}

function updateRepairForm_(formId) {
  const form = FormApp.openById(formId);
  Logger.log('■ ' + form.getTitle());
  requireItem_(form, ANCHOR_TITLE);

  insertAfter_(form, ANCHOR_TITLE, [
    { type: 'paragraph', title: TITLES.SYMPTOM_REPAIR },
    { type: 'radio', title: TITLES.BUDGET, choices: BUDGET_CHOICES_REPAIR_BODY },
  ]);

  addQuestion_(form, { type: 'checkbox', title: TITLES.CONFIRM, choices: [CONFIRM_TEXT_VEHICLE] });
}

function updateBodyForm_(formId) {
  const form = FormApp.openById(formId);
  Logger.log('■ ' + form.getTitle());
  requireItem_(form, ANCHOR_TITLE);

  insertBefore_(form, ANCHOR_TITLE, [
    { type: 'radio', title: TITLES.INSURANCE, choices: ['使う予定', '使わない（自費）', '未定・相談したい'] },
    { type: 'radio', title: TITLES.BUDGET, choices: BUDGET_CHOICES_REPAIR_BODY },
  ]);

  addQuestion_(form, { type: 'checkbox', title: TITLES.CONFIRM, choices: [CONFIRM_TEXT_BODY] });
}

// ===== 共通処理 =====

/**
 * 質問をフォームの最後に追加する（すべて必須）。
 * 同じタイトルの質問がすでにあれば何もしない。
 * @return {{item: GoogleAppsScript.Forms.Item, created: boolean}}
 */
function addQuestion_(form, spec) {
  const existing = findItem_(form, spec.title);
  if (existing) {
    Logger.log('  スキップ（追加済み）: ' + spec.title);
    return { item: existing, created: false };
  }

  let item;
  if (spec.type === 'checkbox') {
    item = form.addCheckboxItem();
  } else if (spec.type === 'radio') {
    item = form.addMultipleChoiceItem();
  } else if (spec.type === 'paragraph') {
    item = form.addParagraphTextItem();
  } else {
    throw new Error('未対応の種類です: ' + spec.type);
  }
  item.setTitle(spec.title);
  if (spec.choices) item.setChoiceValues(spec.choices);
  item.setRequired(true);

  Logger.log('  追加: ' + spec.title);
  return { item: item, created: true };
}

/** 質問を基準の質問の直前に、指定した順で追加する */
function insertBefore_(form, anchorTitle, specs) {
  specs.forEach(function (spec) {
    const result = addQuestion_(form, spec);
    if (!result.created) return;
    form.moveItem(result.item, requireItem_(form, anchorTitle).getIndex());
  });
}

/** 質問を基準の質問の直後に、指定した順で追加する */
function insertAfter_(form, anchorTitle, specs) {
  let prevTitle = anchorTitle;
  specs.forEach(function (spec) {
    const result = addQuestion_(form, spec);
    if (result.created) {
      form.moveItem(result.item, requireItem_(form, prevTitle).getIndex() + 1);
    }
    prevTitle = spec.title;
  });
}

/** 説明文の marker の直後（「。」があればその後ろ）に改行して addition を追記する */
function appendToDescription_(form, marker, addition) {
  const desc = form.getDescription() || '';
  if (desc.indexOf(addition.split('\n')[0]) !== -1) {
    Logger.log('  スキップ（説明文は追記済み）');
    return;
  }
  const pos = desc.indexOf(marker);
  if (pos === -1) {
    Logger.log('  ⚠ 説明文に「' + marker + '」が見つからないため、説明文は変更していません。手動で追記してください。');
    return;
  }
  let insertAt = pos + marker.length;
  if (desc.charAt(insertAt) === '。') insertAt++;
  form.setDescription(desc.slice(0, insertAt) + '\n' + addition + desc.slice(insertAt));
  Logger.log('  説明文に追記しました');
}

/** タイトルで項目を探す（前後の空白・全角/半角カッコの違いは無視） */
function findItem_(form, title) {
  const key = normalizeTitle_(title);
  const items = form.getItems();
  for (let i = 0; i < items.length; i++) {
    if (normalizeTitle_(items[i].getTitle()) === key) return items[i];
  }
  return null;
}

/**
 * 基準にする質問を探す。完全一致がなければ、タイトルが title で始まる質問を使う
 * （例：「問い合わせ内容　使用フルードやその他の要望があれば…」）
 */
function requireItem_(form, title) {
  const key = normalizeTitle_(title);
  const item = findItem_(form, title) || form.getItems().filter(function (it) {
    return normalizeTitle_(it.getTitle()).indexOf(key) === 0;
  })[0];
  if (!item) {
    throw new Error(
      '「' + form.getTitle() + '」に「' + title + '」という質問が見つかりません。' +
      'listAllFormItems で質問のタイトルを確認し、ANCHOR_TITLE を合わせてください（このフォームは何も変更していません）。'
    );
  }
  return item;
}

function normalizeTitle_(title) {
  return String(title || '')
    .replace(/（/g, '(')
    .replace(/）/g, ')')
    .replace(/[\s　]+/g, '');
}

function isRequired_(item) {
  const typed = {
    CHECKBOX: 'asCheckboxItem',
    MULTIPLE_CHOICE: 'asMultipleChoiceItem',
    PARAGRAPH_TEXT: 'asParagraphTextItem',
    TEXT: 'asTextItem',
    LIST: 'asListItem',
    DATE: 'asDateItem',
    SCALE: 'asScaleItem',
  }[String(item.getType())];
  return typed ? item[typed]().isRequired() : false;
}
