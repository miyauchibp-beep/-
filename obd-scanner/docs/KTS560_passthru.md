# Bosch KTS 560 の J2534 パススルー調査（2026-10-31、お店の PC の Claude Code で実施）

結論: **保留**。PC ⇔ KTS のパススルー接続（PassThruOpen）は成功するが、KTS ⇔ 車の通信ができない。
診断はこれまでどおり ELM327 系アダプタ（run_scan.bat、のちに OBDLink）で行う。**ライセンスを回避する方法は取らない。**

## 本体
| 項目 | 内容 |
|---|---|
| 機種 | Bosch KTS 560（ラベルのシリアル 231 833 230、製造 2017-12、8〜28V） |
| Bluetooth 名 | BOSCH_VCI_SN_231833230 |
| VCI Manager の番号 | 203673118（ラベルとは別。中の基板の番号と思われる、未確認） |
| 本体の VCI ソフト | 2.4.184.170（2021-06-17）。「KTS ソフトウェアの有効期限が過ぎています」と表示 |
| ファームウェア | 5.02.18（DDC で最新と表示。更新していない） |
| ESI DVD | 2018/2〜2020/1（本体のソフトより古く使えない） |
| ESI 契約 | 切れている。ヤナセのホットラインは対応外 |

## PC に入れたもの（店主に確認してから実施）
- Bosch Pass-Thru 2.30.1（Bosch 公式、`download.esitronic.de/KTSdownload/kts-setups/Setup_Bosch_Passthru_v2.30.1.exe`、
  6,413,984 バイト、Robert Bosch GmbH の署名は正常）。一緒に VCI Software (KTS) 2.4.184.146、KTS5a-Series Software 3.113、DDC 6.97
- J2534 登録: `PassThruSupport.04.04` に「Bosch KTS350/560/590」、DLL `BVTX4J32.dll`（32 ビット → 32 ビット版 PowerShell で使う）
- スナップオン（Toolplanet）の登録と COM4〜7 は、入れる前後で変わっていない（PC の bosch_passthru/before_install.txt・after_install.txt）

## つながり方
- USB: 「Bosch VCI Device」（USB\VID_0CA0&PID_1202）。中身はネットワーク接続で KTS 側 192.168.171.30
- Bluetooth: PAN（PC 192.168.177.25 ⇔ KTS 192.168.177.2）。パススルーには使わない
- USB だけで車につないでいないと、数分で切れる（差し直せば戻る）

## DDC の設定
1. 「診断 STG のシミュレーション」のチェックを外す
2. 「新規」で KTS560・USB・M アドレス `USN[0.231.833.230]`・Pass-Thru にチェック
3. `RBdevices.ini`: `DeviceList=KTS560_1833230@BSN[231833230];KTS560_1833230@USN[0231833230];`、
   `Active_PASSTHRU=KTS560_1833230@USN[0231833230]`

## できたこと / できていないこと
- ✅ PassThruOpen 成功（設定前は 0x08 デバイスなし）。ファームウェア 2.4.184 / DLL 2.4.184.146 / API 04.04。DDC のテストは「接続は正常」
- ❌ 車へ送れない。Nワゴン JH1（CAN 29bit 500k、ELM327 では読める車）で、CAN 11/29bit、ISO15765・ISO15765_PS（端子 6/14）、
  29bit の応答アドレス複数（18DAF110/11/17/18/28/40）すべて **0x09（送信タイムアウト）**。READ_VBATT は **0V**、
  通信チャンネルで測ると **0x1A**。AC アダプターなし、コネクター・純正ケーブル確認済み、USB を抜いても KTS の電源は入ったまま
  （OBD から給電されている）なのに、DDC は「電源アダプターから電圧供給」と表示

## 原因の候補
| 候補 | 理由 |
|---|---|
| (a) PC の VCI ソフト（2.4.184.146）が本体（.170）より古い | VCI Manager が「PC S/W を更新する」と出す |
| (b) 有効期限切れで、本体が車との通信を止めている（いちばん有力） | パススルーの入り口は通るのに、車側だけ繋がっていないふるまい。「有効期限が過ぎています」の表示 |
| (c) KTS が車の電圧を見ていない | 電圧 0V、DDC は「アダプター給電」と表示 |

## やらないこと
- DDC の「更新」「工場出荷時の設定に戻す」、KTS Manager の「更新を開始する」は押さない
- スナップオン（NANO-BT2、COM4/COM5）と Toolplanet の DLL は使わない
- 書き換えは Bosch の説明書どおり USB だけで（Bluetooth は禁止）。更新するときは安定化電源で支え、途中で切らない
- Bluetooth の「参加する」（PAN）は押さない。NANO-BT2 と OBDII（ELM327）のペアリングは消さない

## Bosch への問い合わせ（https://www.bosch.co.jp/aa/contact/ ）
1. KTS 560（本体ソフト 2.4.184.170、ファームウェア 5.02.18）に合う PC 用 Pass-Thru / VCI ソフトの版
2. ESI の有効期限が切れていても、J2534 パススルーで車と通信できるか
3. 「電源アダプターから供給」と表示され、READ_VBATT が 0V になる理由と直し方
4. ESI 契約を再開する場合の費用

## お店の PC にあるファイル
- bosch_passthru/（インストーラーと、入れる前後の記録）
- j2534_check/j2534_check.ps1（接続・バージョン・電圧の確認だけ）
- j2534_check/j2534_obd.ps1（モード 01/03/09 の読み取りだけ。車台番号は下 4 桁だけ残す）
