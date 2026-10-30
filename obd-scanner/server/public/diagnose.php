<?php
// 診断の受付: POST、Authorization: Bearer <鍵>、本文 {"report": スキャン結果, "vehicle", "symptom", "client"}
declare(strict_types=1);
require __DIR__ . '/../vendor/autoload.php';
require __DIR__ . '/../src/app.php';

set_time_limit(300);  // Claude の診断は 1〜2 分かかる
run_endpoint(fn(PDO $pdo, array $config, array $user, array $body) =>
    handle_diagnose($pdo, $config, $user, $body, 'call_claude'));
