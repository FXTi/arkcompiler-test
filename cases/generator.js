// SPDX-License-Identifier: Apache-2.0
function* seq() { yield 2; yield 3; return 4; }
let s=seq(); print(s.next().value); print(s.next().value); print(s.next().value);
