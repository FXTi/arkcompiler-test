// SPDX-License-Identifier: Apache-2.0
async function* g(){ yield 1; }
let x=g(); print(x.next !== undefined);
