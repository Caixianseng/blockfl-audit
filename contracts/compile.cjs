// SPDX-License-Identifier: MIT
const fs = require('fs');
const path = require('path');
const solc = require('solc');

const filename = 'FLAudit.sol';
const input = {
  language: 'Solidity',
  sources: { [filename]: { content: fs.readFileSync(path.join(__dirname, filename), 'utf8') } },
  settings: {
    optimizer: { enabled: true, runs: 200 },
    evmVersion: 'paris',
    outputSelection: { '*': { '*': ['abi', 'evm.bytecode.object', 'evm.deployedBytecode.object'] } }
  }
};
const output = JSON.parse(solc.compile(JSON.stringify(input), {
  import: (name) => {
    const candidate = path.join(__dirname, 'node_modules', name);
    try { return { contents: fs.readFileSync(candidate, 'utf8') }; }
    catch (_) { return { error: `Cannot resolve import: ${name}` }; }
  }
}));
for (const error of (output.errors || [])) console.error(error.formattedMessage);
if ((output.errors || []).some((error) => error.severity === 'error')) process.exit(1);
const compiled = output.contracts[filename].FLAudit;
const artifact = {
  contractName: 'FLAudit',
  compiler: solc.version(),
  evmVersion: 'paris',
  abi: compiled.abi,
  bytecode: '0x' + compiled.evm.bytecode.object,
  deployedBytecode: '0x' + compiled.evm.deployedBytecode.object
};
fs.mkdirSync(path.join(__dirname, 'artifacts'), { recursive: true });
fs.writeFileSync(path.join(__dirname, 'artifacts', 'FLAudit.json'), JSON.stringify(artifact, null, 2) + '\n');
console.log(`Compiled FLAudit with ${artifact.compiler}; runtime bytes=${compiled.evm.deployedBytecode.object.length / 2}`);
