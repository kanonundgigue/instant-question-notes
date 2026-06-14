// 保護記事の暗号化ツール本体。
// インライン script に置くと、生成テンプレート内の <script> 文字列で HTML パースが
// 途中終了してしまうため、外部ファイルに分離している。
(() => {
  const LOCAL_PASSWORD_PATH = "../PROTECTED_NOTES.local.md";

  // 保護記事テンプレート（notes/_template/article-protected.html と同じ構造）。
  const buildFile = (title, payloadJson) => {
    const safeTitle = title
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
    return `<!doctype html>
<html lang="ja">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>🔒 ${safeTitle} | k-notes</title>
    <meta name="description" content="パスワードで保護された記事です。">
    <meta name="robots" content="noindex">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700&amp;family=Noto+Serif+JP:wght@500&amp;display=swap" rel="stylesheet">
    <link rel="stylesheet" href="../../styles/main.css">
  </head>
  <body>
    <main class="page">
      <article class="article">
        <a class="back-link" href="../../index.html">一覧に戻る</a>

        <div id="note-lock" class="note-lock">
          <h1 class="note-lock__title">🔒 保護された記事</h1>
          <p class="note-lock__desc">この記事はパスワードで保護されています。パスワードを入力すると本文を表示します。</p>
          <form id="note-lock-form" class="note-lock__form">
            <label class="note-lock__label" for="note-pass">パスワード</label>
            <input id="note-pass" class="note-lock__input" type="password" autocomplete="current-password" autofocus required>
            <button class="note-lock__button" type="submit">解錠</button>
            <p id="note-lock-error" class="note-lock__error" role="alert" hidden></p>
          </form>
        </div>

        <div id="note-content" class="note-content" hidden></div>
      </article>
    </main>

    <script type="application/json" id="note-payload">${payloadJson}</script>
    <script src="../../scripts/crypto-core.js" defer></script>
    <script src="../../scripts/decrypt.js" defer></script>
  </body>
</html>
`;
  };

  const $ = (id) => document.getElementById(id);
  const statusEl = $("status");
  const passSourceEl = $("pass-source");
  const outputWrap = $("output");
  const resultEl = $("result");

  const setStatus = (msg, isError = false) => {
    statusEl.textContent = msg;
    statusEl.classList.toggle("tool__status--error", isError);
  };

  const stripPasswordValue = (value) => value
    .trim()
    .replace(/^["'`]+|["'`]+$/g, "")
    .trim();

  const extractPasswordFromLocalNote = (text) => {
    const lines = text.replace(/^\uFEFF/, "").split(/\r?\n/);
    const labelPattern = /^\s*(?:[-*]\s*)?(?:\*\*)?(?:共通)?(?:パスワード|パスフレーズ|password|passphrase)(?:\*\*)?\s*(?:[:：=]|-)\s*(.+?)\s*$/i;

    for (const line of lines) {
      const match = line.match(labelPattern);
      if (match) {
        const password = stripPasswordValue(match[1]);
        if (password) return password;
      }
    }

    let fencedValue = "";
    let inFence = false;
    for (const line of lines) {
      if (/^\s*```/.test(line)) {
        if (inFence && fencedValue.trim()) {
          return stripPasswordValue(fencedValue);
        }
        inFence = !inFence;
        continue;
      }
      if (inFence) {
        fencedValue += fencedValue ? `\n${line}` : line;
      }
    }

    for (const line of lines) {
      const candidate = line.trim();
      if (!candidate) continue;
      if (/^(#|>|<!--|\||```)/.test(candidate)) continue;
      if (/^(password|passphrase|パスワード|パスフレーズ)\s*$/i.test(candidate)) continue;
      return stripPasswordValue(candidate.replace(/^[-*]\s+/, ""));
    }

    return "";
  };

  const loadLocalPassword = async () => {
    try {
      const response = await fetch(LOCAL_PASSWORD_PATH, { cache: "no-store" });
      if (!response.ok) {
        passSourceEl.textContent = "PROTECTED_NOTES.local.md が見つかりません。必要なら手入力してください。";
        return;
      }

      const text = await response.text();
      const password = extractPasswordFromLocalNote(text);
      if (!password) {
        passSourceEl.textContent = "PROTECTED_NOTES.local.md からパスワードを抽出できません。必要なら手入力してください。";
        return;
      }

      $("pass").value = password;
      passSourceEl.textContent = "PROTECTED_NOTES.local.md から自動読込済みです。";
    } catch {
      passSourceEl.textContent = "PROTECTED_NOTES.local.md を読み込めません。localhost 経由で開いているか確認してください。";
    }
  };

  if (!window.crypto || !window.crypto.subtle || !window.NoteCrypto) {
    setStatus("Web Crypto が使えません。https または localhost でこのページを開いてください。", true);
    $("run").disabled = true;
    return;
  }

  loadLocalPassword();

  $("run").addEventListener("click", async () => {
    const title = $("title").value.trim();
    const body = $("body").value;
    const pass = $("pass").value;

    if (!body.trim()) { setStatus("本文 HTML を入力してください。", true); return; }
    if (!pass) { setStatus("パスワードを入力してください。", true); return; }

    setStatus("暗号化中…");
    try {
      const payload = await window.NoteCrypto.encrypt(body, pass);
      const payloadJson = JSON.stringify(payload);
      resultEl.value = buildFile(title || "保護記事", payloadJson);
      outputWrap.hidden = false;
      setStatus("生成しました。全文をコピーして notes 配下に保存してください。");
    } catch (err) {
      setStatus("暗号化に失敗しました: " + (err && err.message ? err.message : err), true);
    }
  });

  $("copy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(resultEl.value);
      setStatus("クリップボードにコピーしました。");
    } catch {
      resultEl.select();
      setStatus("自動コピーに失敗しました。手動で選択してコピーしてください。", true);
    }
  });
})();
