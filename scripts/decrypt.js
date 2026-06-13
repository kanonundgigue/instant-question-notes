// 保護記事ページの制御。
// ページ内に埋め込まれた暗号文を、入力されたパスワードでブラウザ内で復号して本文を表示する。
// 平文・パスワードはどこにも保存しない（メモリ上のみ）。
(() => {
  // 読み込み時点でのみ document.currentScript が自分自身を指すため、ここで URL を捕捉する。
  const selfUrl = (document.currentScript && document.currentScript.src) || location.href;

  const lock = document.getElementById("note-lock");
  const form = document.getElementById("note-lock-form");
  const input = document.getElementById("note-pass");
  const errorBox = document.getElementById("note-lock-error");
  const button = form?.querySelector(".note-lock__button");
  const content = document.getElementById("note-content");
  const payloadEl = document.getElementById("note-payload");

  if (!lock || !form || !input || !content || !payloadEl) {
    return;
  }

  // セキュアコンテキスト以外では Web Crypto が使えない（http://（localhost を除く）や一部の file://）。
  if (!window.crypto || !window.crypto.subtle || !window.NoteCrypto) {
    showError("この環境では復号できません（https もしくは localhost で開いてください）。");
    input.disabled = true;
    if (button) button.disabled = true;
    return;
  }

  let payload;
  try {
    payload = JSON.parse(payloadEl.textContent);
  } catch {
    showError("暗号データの読み込みに失敗しました。");
    return;
  }

  function showError(message) {
    if (!errorBox) return;
    errorBox.textContent = message;
    errorBox.hidden = false;
  }

  function clearError() {
    if (!errorBox) return;
    errorBox.hidden = true;
  }

  // 復号後の本文に含まれるコードブロックにもコピーボタンを付けるため、
  // code-copy.js を動的に読み込む（このスクリプトはDOM走査を1回だけ行う）。
  function enhanceCodeBlocks() {
    const script = document.createElement("script");
    script.src = resolveScriptUrl("code-copy.js");
    document.body.appendChild(script);
  }

  // decrypt.js と同じディレクトリにある code-copy.js の URL を解決する。
  function resolveScriptUrl(name) {
    return new URL(name, selfUrl).href;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearError();

    const password = input.value;
    if (!password) {
      showError("パスワードを入力してください。");
      return;
    }

    if (button) {
      button.disabled = true;
      button.textContent = "復号中…";
    }

    try {
      const html = await window.NoteCrypto.decrypt(payload, password);
      content.innerHTML = html;
      content.hidden = false;
      lock.hidden = true;
      input.value = "";
      // 復号後の本文に <h1> がある場合、ページタイトルをそれに合わせる（任意）。
      const heading = content.querySelector("h1");
      if (heading && heading.textContent) {
        document.title = `${heading.textContent.trim()} | k-notes`;
      }
      enhanceCodeBlocks();
    } catch {
      // AES-GCM はパスワード不一致／改ざんで復号に失敗し例外を投げる。
      showError("パスワードが違います。");
      if (button) {
        button.disabled = false;
        button.textContent = "解錠";
      }
      input.focus();
      input.select();
    }
  });
})();
