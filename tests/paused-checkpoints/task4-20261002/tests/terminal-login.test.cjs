const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname,'../repos/better-mc-remake/server/web/terminal-login.js'),'utf8');
const navigation=[], timers=[];
const context={location:{replace:value=>navigation.push(value)},
  setTimeout:(fn,ms)=>{timers.push({fn,ms});return 1;}};
vm.runInNewContext(source,context);
assert.equal(timers.length,1);
assert.equal(timers[0].ms,10000);
assert.equal(context.fetch,undefined);
assert.equal(context.window,undefined);
assert.deepEqual(navigation,[]);
timers[0].fn();
assert.deepEqual(navigation,['/account.html']);
console.log('Neutral terminal login page timeout fallback passed');
