<?php
// OBD 診断の中継サーバー。Claude の API キーをここだけに置き、
// アプリ・PC 版から受け取ったスキャン結果で診断し、診断と答え合わせをデータベースに保存する。
// 車に何かを送ることはない（スキャン結果を受け取るだけ）。

declare(strict_types=1);

const SERVER_DIR = __DIR__ . '/..';

function load_config(): array
{
    $path = SERVER_DIR . '/config.php';
    if (!is_file($path)) {
        throw new RuntimeException('config.php がありません。config.sample.php をコピーして書き換えてください');
    }
    return require $path;
}

function connect_db(array $config): PDO
{
    $db = $config['db'];
    return new PDO($db['dsn'], $db['user'] ?? null, $db['password'] ?? null, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]);
}

function now(): string
{
    return date('Y-m-d H:i:s');
}

final class HttpError extends Exception
{
    public function __construct(public int $status, string $message)
    {
        parent::__construct($message);
    }
}

function send_json(int $status, array $body): void
{
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode($body, JSON_UNESCAPED_UNICODE);
}

function read_json_body(): array
{
    $body = json_decode((string) file_get_contents('php://input'), true);
    if (!is_array($body)) {
        throw new HttpError(400, 'JSON で送ってください');
    }
    return $body;
}

function bearer_token(): string
{
    $header = $_SERVER['HTTP_AUTHORIZATION'] ?? $_SERVER['REDIRECT_HTTP_AUTHORIZATION'] ?? '';
    return preg_match('/^Bearer\s+(\S+)$/', $header, $m) ? $m[1] : '';
}

/** 鍵から利用者を探す。鍵そのものは保存せず、SHA-256 で照合する。 */
function auth_user(PDO $pdo, string $token): array
{
    if ($token === '') {
        throw new HttpError(401, '鍵がありません');
    }
    $stmt = $pdo->prepare('SELECT * FROM users WHERE token_hash = ? AND active = 1');
    $stmt->execute([hash('sha256', $token)]);
    $user = $stmt->fetch();
    if (!$user) {
        throw new HttpError(401, '鍵が正しくないか、使えなくなっています');
    }
    return $user;
}

/** 車台番号は下 4 桁だけ残す（PC 版・アプリが伏せていなくても、ここで必ず伏せる）。 */
function mask_vin(?string $vin): ?string
{
    if ($vin === null || $vin === '') {
        return $vin;
    }
    $len = mb_strlen($vin);
    return str_repeat('*', max($len - 4, 0)) . mb_substr($vin, -4);
}

function dtc_codes(array $report): array
{
    $codes = [];
    foreach (($report['dtcs'] ?? []) as $group) {
        foreach ((array) $group as $d) {
            if (isset($d['code']) && !in_array($d['code'], $codes, true)) {
                $codes[] = $d['code'];
            }
        }
    }
    return $codes;
}

function verdict_of(?int $rank): string
{
    if ($rank === null) {
        return '';
    }
    return $rank === 1 ? '当たり（1 位の候補）' : ($rank > 1 ? '候補に入っていた（2 位以下）' : '外れ（候補になかった）');
}

/** 同じ車種の ECU（較正 ID が同じ）の答え合わせを、新しい順に（仲間の分も含めて）。 */
function similar_cases(PDO $pdo, array $report, int $limit = 5): array
{
    $cal = (string) ($report['calibration_id'] ?? '');
    if ($cal === '') {
        return [];
    }
    $stmt = $pdo->prepare(
        'SELECT s.vehicle, s.symptom, s.dtcs, a.cause, a.work, a.hit_rank, a.hit_candidate, a.note, a.created_at
         FROM answers a JOIN scans s ON a.scan_id = s.id
         WHERE s.calibration_id = ? ORDER BY a.id DESC LIMIT ' . (int) $limit
    );
    $stmt->execute([$cal]);
    $cases = [];
    foreach ($stmt->fetchAll() as $row) {
        $rank = $row['hit_rank'] === null ? null : (int) $row['hit_rank'];
        $case = [
            'vehicle' => $row['vehicle'], 'symptom' => $row['symptom'],
            'dtcs' => $row['dtcs'] === '' ? [] : explode(' ', $row['dtcs']),
            'cause' => $row['cause'], 'work' => $row['work'], 'verdict' => verdict_of($rank),
            'hit_candidate' => $row['hit_candidate'], 'note' => $row['note'], 'recorded_at' => $row['created_at'],
        ];
        $cases[] = array_filter($case, fn($v) => $v !== null && $v !== '' && $v !== []);
    }
    return $cases;
}

/** PC 版の ai_diag.build_user_message と同じ形。 */
function build_user_message(array $report, string $vehicle, string $symptom, array $past): string
{
    $flags = JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT;
    $parts = [];
    if ($vehicle !== '') {
        $parts[] = "■ 車両情報\n" . $vehicle;
    }
    $parts[] = "■ 症状・お客様の申告\n" . ($symptom !== '' ? $symptom : '（入力なし）');
    $parts[] = "■ OBD2 スキャン結果（JSON）\n" . json_encode($report, $flags);
    if ($past) {
        $parts[] = "■ この店の過去の答え合わせ（同じ較正 ID・車台番号、新しい順）\n" . json_encode($past, $flags);
    }
    return implode("\n\n", $parts);
}

function load_prompt(): array
{
    return json_decode((string) file_get_contents(__DIR__ . '/prompt.json'), true);
}

/**
 * Claude に診断を頼む。戻り値: ['raw' => 届いた文, 'stop_reason', 'model', 'input_tokens', 'output_tokens']
 * テストでは同じ形を返す関数に差し替える。
 */
function call_claude(array $config, string $userMessage): array
{
    $prompt = load_prompt();
    $client = new Anthropic\Client(apiKey: $config['anthropic_api_key']);
    // 安全上の判定で断られたときは、サーバー側で別のモデルに自動で切り替える
    $message = $client->beta->messages->create(
        model: $config['model'] ?? $prompt['model'],
        maxTokens: 16000,
        messages: [['role' => 'user', 'content' => $userMessage]],
        system: $prompt['system'],
        outputConfig: ['effort' => 'high', 'format' => ['type' => 'json_schema', 'schema' => $prompt['schema']]],
        fallbacks: 'default',
        betas: ['server-side-fallback-2026-07-01'],
    );
    $raw = '';
    foreach ($message->content as $block) {
        if ($block->type === 'text') {
            $raw .= $block->text;
        }
    }
    return [
        'raw' => $raw,
        'stop_reason' => (string) $message->stopReason,
        'model' => (string) $message->model,
        'input_tokens' => (int) ($message->usage->inputTokens ?? 0),
        'output_tokens' => (int) ($message->usage->outputTokens ?? 0),
    ];
}

/** POST diagnose.php: {report, vehicle, symptom, client} → {scan_id, diagnosis} */
function handle_diagnose(PDO $pdo, array $config, array $user, array $body, callable $caller): array
{
    $report = $body['report'] ?? null;
    if (!is_array($report)) {
        throw new HttpError(400, 'report（スキャン結果）がありません');
    }
    $limit = (int) ($user['daily_limit'] ?? $config['daily_limit_default'] ?? 20);
    $stmt = $pdo->prepare('SELECT COUNT(*) FROM scans WHERE user_id = ? AND created_at >= ?');
    $stmt->execute([$user['id'], date('Y-m-d 00:00:00')]);
    if ((int) $stmt->fetchColumn() >= $limit) {
        throw new HttpError(429, "今日の診断回数の上限（{$limit} 回）に達しました。明日また使えます");
    }

    unset($report['ai_diagnosis']);
    $report['vin'] = mask_vin($report['vin'] ?? null);
    $vehicle = mb_substr(trim((string) ($body['vehicle'] ?? '')), 0, 200);
    $symptom = trim((string) ($body['symptom'] ?? ''));
    $past = similar_cases($pdo, $report);

    $result = $caller($config, build_user_message($report, $vehicle, $symptom, $past));
    if ($result['stop_reason'] === 'refusal') {
        throw new HttpError(502, 'Claude が回答を控えました。症状の書き方を変えてやり直してください');
    }
    $diagnosis = json_decode($result['raw'], true);
    if (!is_array($diagnosis)) {
        throw new HttpError(502, $result['stop_reason'] === 'max_tokens'
            ? '回答が長すぎて途中で切れました' : '診断を受け取れませんでした');
    }

    $vin = (string) ($report['vin'] ?? '');
    $stmt = $pdo->prepare(
        'INSERT INTO scans (user_id, created_at, client, vehicle, symptom, vin_last4, calibration_id, dtcs,
                            report, diagnosis, model, input_tokens, output_tokens, stop_reason)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'
    );
    $stmt->execute([
        $user['id'], now(), mb_substr((string) ($body['client'] ?? ''), 0, 20), $vehicle, $symptom,
        $vin === '' ? '' : mb_substr($vin, -4), (string) ($report['calibration_id'] ?? ''),
        implode(' ', dtc_codes($report)), json_encode($report, JSON_UNESCAPED_UNICODE),
        json_encode($diagnosis, JSON_UNESCAPED_UNICODE), $result['model'],
        $result['input_tokens'], $result['output_tokens'], $result['stop_reason'],
    ]);
    return ['scan_id' => (int) $pdo->lastInsertId(), 'diagnosis' => $diagnosis, 'past_cases' => count($past)];
}

/** POST answer.php: {scan_id, cause, work, hit_rank, note} → {answer_id} */
function handle_answer(PDO $pdo, array $user, array $body): array
{
    $stmt = $pdo->prepare('SELECT id, diagnosis FROM scans WHERE id = ? AND user_id = ?');
    $stmt->execute([(int) ($body['scan_id'] ?? 0), $user['id']]);
    $scan = $stmt->fetch();
    if (!$scan) {
        throw new HttpError(404, 'その診断が見つかりません');
    }
    $cause = trim((string) ($body['cause'] ?? ''));
    if ($cause === '') {
        throw new HttpError(400, '実際の原因を入れてください');
    }
    $rank = isset($body['hit_rank']) && $body['hit_rank'] !== null ? (int) $body['hit_rank'] : null;
    $candidates = json_decode((string) $scan['diagnosis'], true)['candidates'] ?? [];
    $hit = ($rank !== null && $rank >= 1 && $rank <= count($candidates)) ? $candidates[$rank - 1]['title'] : '';
    $stmt = $pdo->prepare(
        'INSERT INTO answers (scan_id, user_id, created_at, cause, work, hit_rank, hit_candidate, note)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)'
    );
    $stmt->execute([$scan['id'], $user['id'], now(), $cause, (string) ($body['work'] ?? ''), $rank,
                    $hit, (string) ($body['note'] ?? '')]);
    return ['answer_id' => (int) $pdo->lastInsertId(), 'verdict' => verdict_of($rank), 'hit_candidate' => $hit];
}

/** 公開フォルダの各ファイルから呼ぶ共通の入口。エラーは JSON で返す。 */
function run_endpoint(callable $handler): void
{
    try {
        if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
            throw new HttpError(405, 'POST で送ってください');
        }
        $config = load_config();
        $pdo = connect_db($config);
        $user = auth_user($pdo, bearer_token());
        send_json(200, $handler($pdo, $config, $user, read_json_body()));
    } catch (HttpError $e) {
        send_json($e->status, ['error' => $e->getMessage()]);
    } catch (Anthropic\Core\Exceptions\APIStatusException $e) {
        error_log('Claude API: ' . $e->getMessage());
        send_json(502, ['error' => 'Claude API でエラーが出ました。しばらくしてからやり直してください']);
    } catch (Throwable $e) {
        error_log('OBD relay: ' . $e->getMessage());
        send_json(500, ['error' => 'サーバーでエラーが出ました']);
    }
}
