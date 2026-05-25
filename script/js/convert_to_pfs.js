#!/usr/bin/env node
//
// Batch converter: reads KPPY_INPUT lines from stdin,
// outputs tab-separated PFS_UNICODE and PFS_INPUT per line.
//
// Usage: echo "bau1 bau1" | node script/js/convert_to_pfs.js

const path = require('path');
const readline = require('readline');

const lib = require(path.join(
  __dirname, '..', '..', 'lib', 'KonvertToPFS', 'lib',
  'build', 'dist', 'js', 'productionLibrary', 'KonvertToPFS-lib.js'
));
const convert = lib.org.phakfasu.konverttopfs.convertHakfa;

const rl = readline.createInterface({ input: process.stdin });

rl.on('line', (line) => {
  if (!line.trim()) {
    process.stdout.write('\t\n');
    return;
  }
  const pfsUnicode = convert(line, 'KPPY_INPUT', 'PFS_UNICODE');
  const pfsInput = convert(line, 'KPPY_INPUT', 'PFS_INPUT');
  process.stdout.write(`${pfsUnicode}\t${pfsInput}\n`);
});
