<?php
// 利用者（整備士・お店）を追加し、鍵を 1 回だけ表示する。cPanel のターミナルで:
//   php bin/add_user.php "宮内自動車" 30
// 鍵はデータベースには SHA-256 でしか残らないので、表示された鍵を控えて本人に渡す。
declare(strict_types=1);
require __DIR__ . '/../src/app.php';

if (PHP_SAPI !== 'cli') {
    exit(1);
}
$name = $argv[1] ?? '';
if ($name === '') {
    fwrite(STDERR, "使い方: php bin/add_user.php \"名前\" [1 日の上限]\n");
    exit(1);
}
$config = load_config();
$limit = (int) ($argv[2] ?? $config['daily_limit_default'] ?? 20);
$token = bin2hex(random_bytes(24));
$pdo = connect_db($config);
$pdo->prepare('INSERT INTO users (name, token_hash, daily_limit, active, created_at) VALUES (?, ?, ?, 1, ?)')
    ->execute([$name, hash('sha256', $token), $limit, now()]);
echo "追加しました: {$name}（1 日 {$limit} 回まで）\n鍵（この 1 回だけ表示）: {$token}\n";
