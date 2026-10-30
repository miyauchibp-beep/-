<?php
// 中継サーバーのテスト（データベースは SQLite のメモリ上、Claude は決まった答えを返す偽物）
//   php tests/test_server.php
declare(strict_types=1);
require __DIR__ . '/../src/app.php';

$failures = 0;
function check(bool $ok, string $what): void
{
    global $failures;
    echo ($ok ? 'ok   ' : 'FAIL ') . $what . "\n";
    if (!$ok) {
        $failures++;
    }
}

function make_db(): PDO
{
    $pdo = new PDO('sqlite::memory:', null, null, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]);
    // schema.sql（MySQL 用）から、SQLite で読めない書き方を外して使う
    $sql = (string) file_get_contents(__DIR__ . '/../schema.sql');
    $sql = preg_replace('/--[^\n]*/', '', $sql);
    $sql = str_replace(['INT AUTO_INCREMENT PRIMARY KEY', ' DEFAULT CHARSET=utf8mb4'],
                       ['INTEGER PRIMARY KEY AUTOINCREMENT', ''], $sql);
    $sql = preg_replace('/,\s*INDEX \([^)]*\)/', '', $sql);
    $pdo->exec($sql);
    return $pdo;
}

function add_user(PDO $pdo, string $name, string $token, int $limit = 20, int $active = 1): void
{
    $pdo->prepare('INSERT INTO users (name, token_hash, daily_limit, active, created_at) VALUES (?, ?, ?, ?, ?)')
        ->execute([$name, hash('sha256', $token), $limit, $active, now()]);
}

$diagnosis = [
    'summary' => '1 番気筒の失火', 'data_notes' => [], 'cautions' => [], 'customer_explanation' => '点検が必要です',
    'candidates' => [
        ['title' => 'イグニッションコイル不良', 'likelihood' => '高', 'system' => '点火', 'evidence' => ['P0301']],
        ['title' => 'プラグ摩耗', 'likelihood' => '中', 'system' => '点火', 'evidence' => []],
    ],
    'checks' => [], 'repairs' => [], 'urgency' => ['level' => '早めに点検', 'reason' => '触媒を傷める'],
];
$sent = [];
$fake = function (array $config, string $message) use (&$sent, $diagnosis) {
    $sent[] = $message;
    return ['raw' => json_encode($diagnosis, JSON_UNESCAPED_UNICODE), 'stop_reason' => 'end_turn',
            'model' => 'claude-sonnet-5-5', 'input_tokens' => 1200, 'output_tokens' => 800];
};
$config = ['daily_limit_default' => 20];
$report = [
    'protocol' => '6 CAN', 'vin' => 'JH1-1003971', 'calibration_id' => '37805-5Z1-J760U',
    'dtcs' => ['stored' => [['code' => 'P0301']], 'pending' => [['code' => 'P0301']], 'permanent' => []],
    'ai_diagnosis' => ['text' => '古い診断'],
];

// 鍵
$pdo = make_db();
add_user($pdo, '宮内自動車', 'key-1', 2);
add_user($pdo, '止めた人', 'key-off', 20, 0);
$user = auth_user($pdo, 'key-1');
check($user['name'] === '宮内自動車', '正しい鍵で利用者がわかる');
foreach (['', 'wrong', 'key-off'] as $bad) {
    try {
        auth_user($pdo, $bad);
        check(false, "鍵「{$bad}」を断る");
    } catch (HttpError $e) {
        check($e->status === 401, "鍵「{$bad}」を断る（401）");
    }
}
check(strlen((string) $pdo->query('SELECT token_hash FROM users LIMIT 1')->fetchColumn()) === 64, '鍵はハッシュで保存');

// 診断
$out = handle_diagnose($pdo, $config, $user, ['report' => $report, 'vehicle' => 'ホンダ Nワゴン', 'symptom' => '失火',
                                              'client' => 'pc'], $fake);
check($out['scan_id'] === 1 && $out['diagnosis'] === $diagnosis, '診断を返し、番号を付ける');
check(str_contains($sent[0], '*******3971') && !str_contains($sent[0], '1003971'), '車台番号は下 4 桁だけ Claude に送る');
check(!str_contains($sent[0], '古い診断'), '前の診断は送らない');
check(str_contains($sent[0], '■ 症状・お客様の申告' . "\n" . '失火'), 'PC 版と同じ形のメッセージ');
$row = $pdo->query('SELECT * FROM scans WHERE id = 1')->fetch();
check($row['vin_last4'] === '3971' && $row['dtcs'] === 'P0301' && $row['client'] === 'pc', '下 4 桁・故障コードを保存');
check(!str_contains($row['report'], '1003971'), '保存するスキャン結果にも車台番号は残さない');
check((int) $row['input_tokens'] === 1200, '使ったトークン数を保存（費用の確認用）');

// 1 日の上限
handle_diagnose($pdo, $config, $user, ['report' => $report], $fake);
try {
    handle_diagnose($pdo, $config, $user, ['report' => $report], $fake);
    check(false, '1 日の上限を超えたら断る');
} catch (HttpError $e) {
    check($e->status === 429 && count($sent) === 2, '1 日の上限を超えたら断る（Claude を呼ばない）');
}

// 答え合わせ
$ans = handle_answer($pdo, $user, ['scan_id' => 1, 'cause' => 'プラグ摩耗', 'work' => 'プラグ 3 本交換',
                                   'hit_rank' => 2, 'note' => '電極が丸い']);
check($ans['hit_candidate'] === 'プラグ摩耗' && str_contains($ans['verdict'], '候補に入っていた'), '当たった候補を記録');
add_user($pdo, '仲間', 'key-2');
try {
    handle_answer($pdo, auth_user($pdo, 'key-2'), ['scan_id' => 1, 'cause' => 'x']);
    check(false, 'ほかの人の診断には答え合わせできない');
} catch (HttpError $e) {
    check($e->status === 404, 'ほかの人の診断には答え合わせできない');
}

// 仲間の同じ車種の事例が、次の診断に渡る
$friend = auth_user($pdo, 'key-2');
$out = handle_diagnose($pdo, $config, $friend, ['report' => $report], $fake);
$last = end($sent);
check($out['past_cases'] === 1 && str_contains($last, '過去の答え合わせ') && str_contains($last, 'プラグ摩耗'),
      '同じ較正 ID の事例を Claude に渡す（仲間の分も）');

// Claude が断ったとき・JSON が読めないとき
$refuse = fn() => ['raw' => '', 'stop_reason' => 'refusal', 'model' => '', 'input_tokens' => 0, 'output_tokens' => 0];
try {
    handle_diagnose($pdo, $config, $friend, ['report' => $report], $refuse);
    check(false, '断られたら 502');
} catch (HttpError $e) {
    check($e->status === 502, '断られたら 502（保存しない）');
}
check((int) $pdo->query('SELECT COUNT(*) FROM scans')->fetchColumn() === 3, '失敗した診断は保存しない');

// prompt.json は PC 版と同じ指示文・スキーマ
$prompt = load_prompt();
check(str_contains($prompt['system'], 'JSON スキーマ') && $prompt['schema']['type'] === 'object', 'prompt.json を読める');

echo $failures ? "\n{$failures} 件失敗\n" : "\nすべて合格\n";
exit($failures ? 1 : 0);
