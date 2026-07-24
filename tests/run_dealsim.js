/* pytest 계약 테스트용 node 하네스 — stdin 으로 {fn, args} JSON 을 받아
   static/dealsim.js 의 해당 함수를 실행하고 결과 JSON 을 stdout 으로 낸다.
   args 안의 문자열 "__RULES__" 는 data/dealsim_rules.json 로 치환된다(세율 단일 출처). */
'use strict';
const path = require('path');
const fs = require('fs');
const DealSim = require(path.join(__dirname, '..', 'static', 'dealsim.js'));
const rules = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'data', 'dealsim_rules.json'), 'utf8'));

let input = '';
process.stdin.on('data', (c) => { input += c; });
process.stdin.on('end', () => {
  const req = JSON.parse(input);
  const fn = DealSim[req.fn];
  if (typeof fn !== 'function') {
    process.stderr.write('unknown fn: ' + req.fn);
    process.exit(2);
  }
  const args = (req.args || []).map((a) => (a === '__RULES__' ? rules : a));
  process.stdout.write(JSON.stringify(fn.apply(null, args)));
});
