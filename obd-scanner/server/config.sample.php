<?php
// 中継サーバーの設定。config.php という名前でコピーし、サーバーの中だけで書き換える（GitHub には入れない）。
//   cp config.sample.php config.php
return [
    // Claude Console で作った API キー（Console の「利用上限」も必ず設定する）
    'anthropic_api_key' => 'sk-ant-ここにキー',
    'model' => 'claude-sonnet-5-5',
    // cPanel の Database Wizard で作ったデータベース
    'db' => [
        'dsn' => 'mysql:host=localhost;dbname=jytgvshy_obd;charset=utf8mb4',
        'user' => 'jytgvshy_obd_app',
        'password' => 'ここにパスワード',
    ],
    // 1 人 1 日の診断回数の上限（利用者ごとに users.daily_limit で変えられる）
    'daily_limit_default' => 20,
];
