<?php
// 動いているかの確認だけ（鍵いらず、データベースにも触らない）
header('Content-Type: application/json; charset=utf-8');
echo json_encode(['ok' => true]);
