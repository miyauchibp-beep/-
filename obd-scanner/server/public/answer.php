<?php
// 答え合わせの受付: POST、Authorization: Bearer <鍵>、本文 {"scan_id", "cause", "work", "hit_rank", "note"}
declare(strict_types=1);
require __DIR__ . '/../vendor/autoload.php';
require __DIR__ . '/../src/app.php';

run_endpoint(fn(PDO $pdo, array $config, array $user, array $body) => handle_answer($pdo, $user, $body));
