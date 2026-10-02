(() => {
  // Native CEF submits the one-use ticket directly; this page never receives it in JavaScript.
  // Only the native client can retry an expired exchange. Never redirect to a login flow.
  setTimeout(() => { document.body.textContent = '账户会话暂时无法建立，请返回终端后重试'; }, 10000);
})();
