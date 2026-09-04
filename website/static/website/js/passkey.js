/* パスキー（WebAuthn）のブラウザ側処理
 *
 * QRの表示・近接通信・顔認証はすべてブラウザとOSがやる。
 * ここがやるのは「サーバーとブラウザの間でデータを受け渡す」ことだけ。
 *
 * ⚠️ HTTPSでないと navigator.credentials は使えない（localhostのみ例外）。
 */
(function () {
  'use strict';

  // ---- base64url とバイナリの相互変換（WebAuthn APIはバイナリを要求する） ----
  function b64urlToBuf(s) {
    s = s.replace(/-/g, '+').replace(/_/g, '/');
    s += '='.repeat((4 - (s.length % 4)) % 4);
    var bin = atob(s);
    var buf = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
    return buf.buffer;
  }
  function bufToB64url(buf) {
    var bytes = new Uint8Array(buf), s = '';
    for (var i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
    return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }

  function csrf() {
    var m = document.cookie.match(/csrftoken=([^;]+)/);
    if (m) return m[1];
    var el = document.querySelector('[name=csrfmiddlewaretoken]');
    return el ? el.value : '';
  }

  function post(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: { 'X-CSRFToken': csrf(), 'Content-Type': 'application/json' },
      body: body || '{}',
    });
  }

  window.PasskeySupported = function () {
    return !!(window.PublicKeyCredential && navigator.credentials);
  };

  // ---- 登録（設定画面から呼ぶ） ----
  window.passkeyRegister = async function (name, onDone, onError) {
    try {
      var res = await post('/passkey/register/options/');
      if (!res.ok) throw new Error((await res.json()).error || '準備に失敗しました');
      var opts = await res.json();

      opts.challenge = b64urlToBuf(opts.challenge);
      opts.user.id = b64urlToBuf(opts.user.id);
      (opts.excludeCredentials || []).forEach(function (c) { c.id = b64urlToBuf(c.id); });

      var cred = await navigator.credentials.create({ publicKey: opts });
      var payload = {
        id: cred.id,
        rawId: bufToB64url(cred.rawId),
        type: cred.type,
        response: {
          clientDataJSON: bufToB64url(cred.response.clientDataJSON),
          attestationObject: bufToB64url(cred.response.attestationObject),
        },
      };
      var vr = await post('/passkey/register/verify/?name=' + encodeURIComponent(name || ''),
                          JSON.stringify(payload));
      var data = await vr.json();
      if (!vr.ok) throw new Error(data.error || '登録に失敗しました');
      onDone(data);
    } catch (e) {
      onError(e.name === 'NotAllowedError' ? '操作がキャンセルされました。' : (e.message || String(e)));
    }
  };

  // ---- ログイン（ログイン画面から呼ぶ） ----
  window.passkeyLogin = async function (nextUrl, onError) {
    try {
      var res = await post('/passkey/auth/options/');
      if (!res.ok) throw new Error((await res.json()).error || '準備に失敗しました');
      var opts = await res.json();

      opts.challenge = b64urlToBuf(opts.challenge);
      (opts.allowCredentials || []).forEach(function (c) { c.id = b64urlToBuf(c.id); });

      // ここでブラウザが顔認証を求める。PCなら「別の端末を使う」でQRが出る
      var cred = await navigator.credentials.get({ publicKey: opts });
      var payload = {
        id: cred.id,
        rawId: bufToB64url(cred.rawId),
        type: cred.type,
        response: {
          clientDataJSON: bufToB64url(cred.response.clientDataJSON),
          authenticatorData: bufToB64url(cred.response.authenticatorData),
          signature: bufToB64url(cred.response.signature),
          userHandle: cred.response.userHandle ? bufToB64url(cred.response.userHandle) : null,
        },
      };
      var vr = await post('/passkey/auth/verify/?next=' + encodeURIComponent(nextUrl || ''),
                          JSON.stringify(payload));
      var data = await vr.json();
      if (!vr.ok) throw new Error(data.error || 'ログインに失敗しました');
      window.location.href = data.redirect || '/';
    } catch (e) {
      onError(e.name === 'NotAllowedError' ? '操作がキャンセルされました。' : (e.message || String(e)));
    }
  };
})();
