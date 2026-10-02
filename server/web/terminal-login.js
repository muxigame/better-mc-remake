(() => {
  // Native CEF submits the one-use ticket directly; this page never receives it in JavaScript.
  setTimeout(() => location.replace('/account.html'), 10000);
})();
