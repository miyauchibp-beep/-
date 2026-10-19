# 相談ボックスに「ATF・CVTF・オイル交換の相談」ボタンを追加する手順

## 1. ボックスの作成場所(特定済み)
- **外観 → ウィジェット → 記事下のウィジェットエリア**(テーマの `bottom_ad`)にある
  **「カスタムHTML」ブロック(`block-3`)** で作られている。
- WPCode・パターン・テーマのファイルではない。
- このエリアは**投稿(記事)ページの本文の下**に出る。固定ページとトップページには出ない。
  確認した記事:/inquiry-2/、/oil-atf-cvtf/、/serena-7/、/solio/(PC・スマホの両方)

## 2. リンク先
既存のボタンは、どれもフォームを埋め込んだサイト内の記事にリンクしている。
- 鈑金・塗装 → https://miyauchi-bp.com/bodywork-paint/
- 整備・修理・車検 → https://miyauchi-bp.com/inquiry-2/

オイル交換フォームにも、フォームを埋め込んだ記事がすでにある。
- **https://miyauchi-bp.com/oil-atf-cvtf/**(「オイル ATF CVTF についての問合わせ」)
- 埋め込まれているフォームは `1FAIpQLSdGsKILA-UNruvaCbBVg-7_9Y5ePK0orgVqUMw_7v1ZMuI9dw`。
  これは編集用ID `1RT6da8uXF8E4oaDR7Kt7U2e7lQhNnkB9k1H5FcdNif4` の回答用URLと同じ(確認済み)。
- このページに金額は書かれていない(確認済み)。

既存のボタンと形式をそろえるため、ボタンのリンク先は `/oil-atf-cvtf/` にする。
フォームを直接開きたい場合の回答用URL:
https://docs.google.com/forms/d/e/1FAIpQLSdGsKILA-UNruvaCbBVg-7_9Y5ePK0orgVqUMw_7v1ZMuI9dw/viewform

## 3. 変更前のコード(控え)
```html
<p class="more-works-links" style="margin-bottom:1em;">ほかの施工例を見る：<a href="https://miyauchi-bp.com/atf-cvtf-works/">ATF・CVTF交換</a> ｜ <a href="https://miyauchi-bp.com/bodywork-works/">鈑金・塗装</a> ｜ <a href="https://miyauchi-bp.com/repair-works/">整備・修理</a></p>
<div class="miyauchi-contact-box" style="margin:24px 0;padding:16px;border:2px solid #5d8ac1;border-radius:8px;">
<p style="margin:0 0 8px;font-weight:bold;text-align:center;">お見積り・ご相談はこちらから</p>
<a href="https://miyauchi-bp.com/bodywork-paint/" style="display:block;margin:10px 0;padding:14px 10px;background:#5d8ac1;color:#fff;text-align:center;font-weight:bold;border-radius:6px;text-decoration:none;">鈑金・塗装の相談</a>
<a href="https://miyauchi-bp.com/inquiry-2/" style="display:block;margin:10px 0;padding:14px 10px;background:#4a6fa0;color:#fff;text-align:center;font-weight:bold;border-radius:6px;text-decoration:none;">整備・修理・車検の相談</a>
<a href="https://line.me/R/ti/p/%40sku4081c" style="display:block;margin:10px 0;padding:14px 10px;background:#06c755;color:#fff;text-align:center;font-weight:bold;border-radius:6px;text-decoration:none;">LINEで相談</a>
</div>
```

## 4. 追加する1行
見出し「お見積り・ご相談はこちらから」の `</p>` のすぐ後、つまり「鈑金・塗装の相談」の行の**上**に、次の1行を追加する。

```html
<a href="https://miyauchi-bp.com/oil-atf-cvtf/" style="display:block;margin:10px 0;padding:14px 10px;background:#c8641e;color:#fff;text-align:center;font-weight:bold;border-radius:6px;text-decoration:none;">ATF・CVTF・オイル交換の相談</a>
```

- 色 `#c8641e`(オレンジ):既存の水色・紺・緑と区別でき、オイルを連想させる。
  白い文字とのコントラスト比は3.96で、既存の水色ボタン(3.58)より読みやすい。
- スタイルは「整備・修理・車検の相談」ボタンと同じで、変えたのは色・文字・リンク先だけ。

## 5. 作業手順
1. 管理画面 → 外観 → ウィジェット → 記事下のエリアにある「カスタムHTML」ブロックを開く
2. 中身をすべてコピーして控える(上の3と同じ内容のはず)
3. 4の1行を「鈑金・塗装の相談」の行の上に貼り付け → 「更新」
4. 確認:任意の記事(例:/solio/)を PC とスマホで開き、ボタンが4つ並んでいて、
   一番上のオレンジのボタンからオイル交換フォームのページが開くことを確かめる
