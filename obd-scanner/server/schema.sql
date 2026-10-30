-- 中継サーバーのデータベース（phpMyAdmin の「インポート」で読み込む）
-- お客様の名前・電話番号は持たない。車台番号は下 4 桁だけ。

CREATE TABLE users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,              -- 整備士・お店の名前（呼び名）
    token_hash CHAR(64) NOT NULL UNIQUE,     -- 鍵の SHA-256（鍵そのものは保存しない）
    daily_limit INT NOT NULL DEFAULT 20,     -- 1 日の診断回数の上限
    active TINYINT NOT NULL DEFAULT 1,       -- 0 にすると使えなくなる
    created_at DATETIME NOT NULL
) DEFAULT CHARSET=utf8mb4;

CREATE TABLE scans (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    created_at DATETIME NOT NULL,
    client VARCHAR(20) NOT NULL DEFAULT '',  -- pc / android
    vehicle VARCHAR(200) NOT NULL DEFAULT '',
    symptom TEXT,
    vin_last4 VARCHAR(4) NOT NULL DEFAULT '',
    calibration_id VARCHAR(64) NOT NULL DEFAULT '',
    dtcs VARCHAR(500) NOT NULL DEFAULT '',   -- 故障コードを空白区切りで（検索用）
    report MEDIUMTEXT NOT NULL,              -- スキャン結果（JSON）
    diagnosis MEDIUMTEXT,                    -- Claude の診断（JSON）
    model VARCHAR(50) NOT NULL DEFAULT '',
    input_tokens INT NOT NULL DEFAULT 0,
    output_tokens INT NOT NULL DEFAULT 0,
    stop_reason VARCHAR(30) NOT NULL DEFAULT '',
    INDEX (user_id, created_at),
    INDEX (calibration_id)
) DEFAULT CHARSET=utf8mb4;

CREATE TABLE answers (
    id INT AUTO_INCREMENT PRIMARY KEY,
    scan_id INT NOT NULL,
    user_id INT NOT NULL,
    created_at DATETIME NOT NULL,
    cause TEXT NOT NULL,                     -- 実際の原因
    work TEXT,                               -- 作業・交換部品
    hit_rank INT,                            -- 当たった候補の順位（0 = どれでもない）
    hit_candidate VARCHAR(200) NOT NULL DEFAULT '',
    note TEXT,                               -- 決め手・メモ
    INDEX (scan_id)
) DEFAULT CHARSET=utf8mb4;
