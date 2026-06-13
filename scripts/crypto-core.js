// 保護記事の暗号化 / 復号の共通ロジック。
// ブラウザ標準の Web Crypto API のみを使い、外部ライブラリやビルドツールに依存しない。
// 方式: PBKDF2(SHA-256) で鍵導出 → AES-GCM(256bit) で暗号化。
// この仕組みは「公開されるのは暗号文だけ」を前提とする。強度はパスワードの強さに依存する。
(() => {
  const enc = new TextEncoder();
  const dec = new TextDecoder();

  // PBKDF2 の反復回数。大きいほど総当たり攻撃に強くなるが、解錠時の待ち時間が増える。
  const ITERATIONS = 250000;

  // ArrayBuffer / TypedArray と base64 文字列の相互変換。
  // 大きなデータでもコールスタックを溢れさせないよう分割して処理する。
  const toBase64 = (buffer) => {
    const bytes = new Uint8Array(buffer);
    const chunkSize = 0x8000;
    let binary = "";
    for (let i = 0; i < bytes.length; i += chunkSize) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize));
    }
    return btoa(binary);
  };

  const fromBase64 = (base64) => {
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) {
      bytes[i] = binary.charCodeAt(i);
    }
    return bytes;
  };

  // パスワードと salt から AES-GCM 用の鍵を導出する。
  const deriveKey = async (password, salt, iterations) => {
    const baseKey = await crypto.subtle.importKey(
      "raw",
      enc.encode(password),
      "PBKDF2",
      false,
      ["deriveKey"],
    );
    return crypto.subtle.deriveKey(
      { name: "PBKDF2", salt, iterations, hash: "SHA-256" },
      baseKey,
      { name: "AES-GCM", length: 256 },
      false,
      ["encrypt", "decrypt"],
    );
  };

  // 平文を暗号化し、復号に必要な情報をまとめたペイロードを返す。
  const encrypt = async (plaintext, password) => {
    const salt = crypto.getRandomValues(new Uint8Array(16));
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const key = await deriveKey(password, salt, ITERATIONS);
    const ciphertext = await crypto.subtle.encrypt(
      { name: "AES-GCM", iv },
      key,
      enc.encode(plaintext),
    );
    return {
      v: 1,
      alg: "AES-GCM",
      kdf: "PBKDF2-SHA256",
      iter: ITERATIONS,
      salt: toBase64(salt),
      iv: toBase64(iv),
      ct: toBase64(ciphertext),
    };
  };

  // ペイロードとパスワードから平文を復号する。パスワード不一致時は例外を投げる。
  const decrypt = async (payload, password) => {
    const salt = fromBase64(payload.salt);
    const iv = fromBase64(payload.iv);
    const key = await deriveKey(password, salt, payload.iter || ITERATIONS);
    const plaintext = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv },
      key,
      fromBase64(payload.ct),
    );
    return dec.decode(plaintext);
  };

  window.NoteCrypto = { encrypt, decrypt, ITERATIONS };
})();
